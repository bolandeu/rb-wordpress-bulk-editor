# AGENTS.md — WordPress Bulk Editor

Bidirectional CLI: Excel/JSON ↔ WordPress. Async-first, project-aware, опциональный Pillow.

---

## Инварианты — не нарушать

**ExcelReader не сбрасывает индекс.**
`read_sheet()` возвращает DataFrame с оригинальными label'ами строк.
`ExcelWriter.update_row()` грузит свой DataFrame отдельно и ищет строку по этому label'у.
`reset_index()` сломает writeback — строки будут записаны не туда.

**ExcelWriter сохраняет только при изменениях.**
`_dirty: bool = False` в `__init__`. Ставится в `True` в `update_row` (только если колонка найдена), `append_rows`, `replace_rows`.
`save()` начинает с `if not self._dirty: return` — иначе `pd.ExcelWriter(mode="w")` уничтожит форматирование, комментарии и формулы.

**`WordPressConfig.from_credentials()` — единственная точка сборки конфига.**
Только этот метод читает `WP_VERIFY_SSL` из `Settings`.
Прямой вызов `WordPressConfig(...)` конструктором теряет SSL-настройки.
Применяется везде: `AppConfig.from_excel`, tool/fetch/export commands.

**`@async_retry` — не заменять на tenacity.**
Кастомная реализация в `src/utils/retry.py`. Знает иерархию исключений проекта:
ретраит `NetworkError`, `ServerError`, HTTP 429/502/503/504;
никогда не ретраит `AuthenticationError`, `NotFoundError`.
Если исключение несёт атрибут `retry_after` (его ставит провайдер из заголовка
`Retry-After`), пауза берётся оттуда.

**Весь HTTP идёт через `src/providers/`.**
`HttpProvider` — общая база (маппинг статусов, ретраи, circuit breaker,
User-Agent, таймаут, шов `transport` для `httpx.MockTransport`),
`WordPressHttpProvider` — специфика WP (Basic Auth, `verify_ssl`, загрузка медиа).
`WordPressClient` — тонкая обёртка над провайдером, интерфейс прежний.
Не создавать свой `httpx.AsyncClient` в endpoint-ах: загрузка медиа тоже идёт
через провайдер (`upload_binary`), иначе теряются авторизация, retry и CB.

**Необратимое действие — только через гейт.**
Сначала `--dry-run` (превью, без вопросов и без изменений), затем
`output.require_confirmation(ctx, описание, confirm)` — и только потом действие.
Свой `typer.confirm` в командах не писать: под `--non-interactive`, в `--json`
и когда stdin не терминал, вопрос должен превращаться в `INVALID_ARGUMENTS` (exit 2),
а не в зависший prompt. Гейт вызывается **после** проверки `--dry-run`, не до.

Что гейтится сейчас: `bulk-delete`; `options` (запись настроек на сайт и
пересборка непустого таба `options`); `fetch-media` (пересборка непустого таба
`media`); `fetch` (перезапись существующего xlsx / непустого JSON-таба); `sync`/`async` —
только необратимые пути (`media`-строки с `action=delete` и `--delete-media`:
там `force=True`, мимо корзины), см. `_confirm_irreversible` в `sync_commands.py`.
`--delete-media` **не зависит от источника** (featured-изображения удаляются у
любой записи с `action=delete`, хоть из Excel, хоть из JSON) — гейт вызывается в
обеих ветках `_open_plan`; Excel-специфичен только подсчёт media-строк.
Удаление контента идёт в корзину (`force=False`) и подтверждения не требует.

**Цепочка credentials — одна реализация.**
`utils/auth.resolve_credentials()` возвращает значения **и источник каждого поля**
(`cli`/`env`/`keychain`/`.env`). На неё опираются и `load_credentials_from_env`
(реальные команды), и `doctor`. Вторую реализацию цепочки не заводить: разъедутся —
и диагностика начнёт врать. `.env` без явного пути ищется `find_dotenv(usecwd=True)`
(от папки пользователя, не от файла вызывающего кода).

**Секреты — keychain или окружение, не файлы проекта.**
`src/credentials/manager.py` (keyring, provider = домен сайта).
Порядок: CLI-флаг → переменная окружения → keychain → `.env` (legacy).

**`.env` antagor понимается как есть.** Тот же `.env` проекта
(`antagor/data/projects/<name>/.env`) должен работать и при вызове wp-bulk из
antagor, и вручную из терминала — поэтому `SITE_DOMAIN` / `SITE_REST_API_USER` /
`SITE_REST_API_PASSWORD` — запасные имена `WP_DOMAIN` / `WP_API_USER` /
`WP_API_TOKEN` (`_ANTAGOR_ENV_ALIASES` в `utils/auth.py`, внутри той же единственной
цепочки). Свои имена главнее; алиасы игнорируются, если `SITE_CMS` задан и не
`wordpress` — чтобы не подхватить доступы сайта на другой CMS.

**`SITE_PROD` — не алиас и не станет им.** В `.env` antagor рядом лежат
`SITE_PROD` (живой сайт) и `SITE_DOMAIN` (локальная копия, с ней идёт вся работа).
Живой сайт трогается только в сценариях, которые владелец данных назвал явно,
поэтому `SITE_PROD` в `_ANTAGOR_ENV_ALIASES` не добавлять: при одном `SITE_PROD`
домен не находится (exit 3), а не подставляется прод. Замок —
`TestAntagorAliases::test_site_prod_is_never_a_domain`.

**`action` нормализуется в одном месте.** `constants.normalize_action()`:
`a`→`add`, `u`/`e`/`edit`→`update`, `d`→`delete`, `s`/`0`→`skip`. Все сравнения
`action` в коде идут через неё — не сравнивать сырое значение со строкой.

**pandas 2.x StringDtype.**
При записи в колонку с `StringDtype` (`is_string_dtype()`) значение должно быть `str`.
Иначе pandas бросает TypeError. `_coerce_for_column()` в `writer.py` делает каст.

**HTML → Gutenberg — соседний пакет `html_to_gutenberg`, своего конвертера нет.**
`ContentProcessor` (`content_path` → `content`) вызывает
`HtmlToGutenberg().convert_report()` и пишет его `warnings` в лог. Незнакомые теги
библиотека не выбрасывает, а сохраняет блоком `wp:html`, обёртки (`div`/`section`)
раскрывает. Чинить конвертацию — в `tools/html-to-gutenberg`, не здесь.
Источник в `[tool.uv.sources]` — git (как у `llmkit`), чтобы wp-bulk ставился
из git на любой машине. Новая версия конвертера — push в его репозиторий,
затем `uv lock --upgrade-package html_to_gutenberg` здесь.

На машине разработки wp-bulk, `html-to-gutenberg` и `llmkit` стоят editable из
соседних папок — правки любого подхватываются без переустановки:

```bash
uv tool install --editable . --no-sources \
  --with-editable ../html-to-gutenberg --with-editable ../llmkit --force
```

`--no-sources` обязателен: иначе git-источники из `pyproject.toml` конфликтуют с
`--with-editable` («conflicting URLs»).

**Pillow — ленивый импорт.**
`image_processor.py` импортирует PIL внутри `__init__`, не на уровне модуля.
Не переносить на уровень модуля — Pillow опционален, его может не быть.

---

## Поток данных

```
sync/async:
  Excel
    → ExcelReader.read_sheet()          # DataFrame, индекс не сбрасывается
    → FieldDetector.detect(columns)     # categories: system|taxonomy|acf|nested|seo|content
    → ContentMapper.map_row(row)        # Excel row → WP API payload
    → WordPressClient (async httpx)
    → ResponseMapper.map(response)      # API response → dict для writeback
    → ExcelWriter.update_row_after_sync(idx, data)   # idx = оригинальный label

fetch:
  WordPressDiscovery.discover()         # все типы, таксономии, ACF поля сайта
    → PostsEndpoint.list()
    → ResponseMapper
    → ExcelWriter (создаёт или обновляет листы)
    | json_fetch.build_site_tab → json_source.write_tab   # --file *.json / --to-json

update (WP → файл по id, WordPress только читается):
  RecordSource (Excel/JSON) → sync/field_pull.pull_fields → RecordWriter.update_record
```

---

## Источники данных: Excel и JSON

`sync`/`async` читают либо xlsx, либо json — **источник выбирается по расширению
`--file`**, отдельного `--format` нет. Дальше пайплайн общий: `RecordSource` →
группировка по дискриминатору (`sync_record_groups`) → content/taxonomy sync →
`RecordWriter`.

| | Excel (`.xlsx`) | JSON (`.json`) |
| --- | --- | --- |
| Набор данных | лист | ключ в `tabs` |
| Тип записи | имя листа на весь лист | поле `type`/`taxonomy` у каждой записи |
| Имя записи | `title` / `name` | так же |
| Ключ writeback | label строки pandas | `_row_num` (иначе позиция в массиве) |
| Классы | `ExcelRecordSource/Writer` | `JsonRecordSource/Writer` |

Форма JSON-файла:

```json
{"project": "mw", "tabs": {
  "options":  [{"_row_num": 2, "key": "phone", "value": "+7"}],
  "mw.ru":    [{"_row_num": 2, "action": "add", "type": "rb_services",
                "title": "…", "seo.title": "…", "acf.f1": "…",
                "rb_services_cat": "Перевозки"}]}}
```

**Значение-путь → содержимое файла** (`src/utils/file_values.py`, подключён в
`ContentMapper.map_row` через `file_resolver`): значение, оканчивающееся на
`.html .htm .md .markdown .txt` (закрытый список — иначе `example.com` сошёл бы за
файл), заменяется содержимым файла. Сырой HTML, без конвертации в Gutenberg — в
отличие от `content_path`, который идёт только в `content`. Относительный путь —
от папки файла данных с запретом выхода через `..`, абсолютный — как есть.
Нет файла — `ValidationError` для записи. Поля-идентификаторы (`slug`,
`custom_permalink`, `title`…) не трогаются: `IDENTITY_FIELDS`.

`_row_num`/`type`/`taxonomy` — метаданные источника: в payload WordPress не
уходят, но в файле остаются. Запись, где заполнены оба или ни одного из
`type`/`taxonomy`, — ошибка (`RecordSourceError`, exit 2) с указанием таба и
номера записи; весь таб проверяется до отправки первой записи.

**Авто-поиск файла данных** (`ProjectContext.find_data_file()`): xlsx **или**
json в корне проекта, внутри формата приоритет у файла с именем проекта. Если в
папке лежат оба формата — ошибка со списком кандидатов и требованием `--file`:
молча выбирать источник нельзя.

Кандидаты отбирает общий `_candidates()` (им же пользуется `find_excel()`):
`~$*.xlsx` (локи Excel) отбрасываются, а json проверяется на форму
(`json_source.is_data_file()` — парсится и есть объект `tabs`). Посторонний json
в папке проекта (service-account Google и т.п.) не становится ни «данными», ни
поводом объявить форматы неоднозначными.

**JSON — транспортный формат, Excel — интерфейс человека.** Новая команда,
читающая или пишущая данные сайта, умеет JSON сразу.

**Оба формата:** `sync`, `async`, `update`, `validate`, `fetch`, `options`
(таб `options`, записи `{key, value}`, прочие поля вроде `label` игнорируются),
`fetch-media` (таб `media`).
**Только Excel** (JSON-эквивалента пока нет): `reshape`, `llm`, `export*`, `vlook`;
синхронизация строк `media` (`MediaSync`) — тоже только из листа Excel.

**Хранилище табов — `src/records/store.py`.** Таб = имя + список записей-словарей;
`open_store(path)` — единственное место, где по расширению выбирается носитель
(`JsonTabStore`, `ExcelTabStore`). Новый носитель (БД и т.п.) — новый адаптер в
`_ADAPTERS`, команды не меняются. `write_tab` заменяет таб целиком, остальные
не трогает; `_row_num` — забота JSON-адаптера (нумерует с 2), в Excel колонкой
не пишется. На хранилище сейчас: `options`, `fetch-media`, JSON-ветка `fetch`.
`sync`/`update`/`validate` и Excel-ветка `fetch` пока идут через свои
`RecordSource`/`ExcelWriter` — переводить на хранилище, не заводя третий путь.
Запись таба в записи на `media` не содержит `type` (всегда `attachment`): в JSON
это дискриминатор контента.

Выгрузка с сайта (`fetch`, `options --fetch`, `fetch-media`) не пишет в реестр
проекта `<проект>/<проект>.json` — `common.refuse_project_registry`.

Выбор источника и табов (`is_json_source`, `require_json_tabs`) — общий модуль
`src/records/selection.py`; своих проверок `--sheets` в командах не заводить.

### Обратное направление для JSON: `update`, `fetch`, `validate`

- **`update`** — один движок для обоих форматов (`sync/field_pull.py`) поверх
  `RecordSource`/`RecordWriter.update_record`. Термин и контент в одном табе
  различаются `Record.is_taxonomy`: запрос идёт в endpoint таксономии или типа
  записи. В Excel пишутся только существующие колонки, в JSON недостающие поля
  добавляются. `--dry-run` writer не открывает (тот же инвариант, что у `sync`).
- **`fetch` в JSON** (`--file *.json` или `--to-json` → `<проект>/wp-fetch/<домен>.json`)
  — один таб на сайт (имя = домен или `--tab`), `records/json_fetch.py`. Термины
  идут **первыми**: `sync` обрабатывает группы в порядке первого появления.
  `type` — REST-имя типа (`posts`, `rb_services`), а не поле `type` ответа
  (`post`): иначе `sync` постучится в несуществующий `/wp/v2/post`. ACF/SEO —
  dot-нотацией; списки объектов (повторители ACF, JSON-LD) и `seo.schema` не
  выгружаются — табличной моделью их не отредактировать.
- **Таб пересобирается целиком**, `_row_num` с 2. Дописывать нельзя: повторный
  fetch задвоил бы записи по `id`. Непустой таб перезаписывается только через
  `require_confirmation`. Повторный запуск идемпотентен.
- **fetch не пишет в реестр** `<проект>/<проект>.json`: antagor пересобирает его
  из Google-таблицы и молча теряет записи, которых там нет.
- **Термин обратно на сайт** (`TaxonomySyncBase._build_payload`) — по правилам
  контента: `acf.*`/`seo.*` раскладываются во вложенные объекты, пустые не
  отправляются (в общем табе у термина пусты колонки контента — они не должны
  затирать значения на сайте), `seo.robots.*` пустые → true, значение-путь →
  содержимое файла, `SYSTEM_COLUMNS` (`date`, `link`…) в API не идут.
- **`validate` для JSON** — `JsonRecordSource.problems()` (все структурные
  ошибки сразу) + общая проверка перед отправкой (см. ниже). Сайт — только
  если есть доступы (или `--offline`), см. «Таксономии — с сайта».

### Проверка перед отправкой (preflight)

`src/records/preflight.py`, одна для Excel и JSON (работает на `Record`).
Её показывает `validate`, и она же **автоматически идёт перед `sync`/`async`**
(`_preflight` в `sync_commands.py`, внутри `_open_plan` — после подключения и
чтения схемы сайта, но до подтверждения и до первого изменения на сайте, в том
числе при `--dry-run`). Ошибки → exit 2 со списком всех проблем,
предупреждения — в лог. Отключается `--skip-checks`.

- **Имена полей.** Всё, кроме `acf.*` / `acf_*` / `meta.*`, должно быть в
  `KNOWN_FIELDS`, `DEFAULT_SEO_FIELDS`, `WP_SEO_FIELDS` или `WP_EXTRA_FIELDS`
  (свои поля проекта). Опечатка иначе молча уходит в payload, а WordPress её
  молча игнорирует — так в `mw.json` жил `seo.descripton`. Подсказка похожего
  имени — `difflib`.
- **Имена таксономий** — строчные, части через `_`/`-` (колонки и записи-термины).
- **Сверка с сайтом** (`FieldRules.site`, схема из `wordpress/site_schema.py`):
  колонка-таксономия есть на сайте; привязана к типу записи (иначе WordPress
  молча проигнорирует поле — проверяется только при непустом значении); `type`
  и `taxonomy` записей существуют. К опечатке — похожее имя с сайта.
  Сообщение одно на (проблема), с числом записей.
- **Значения** (только у `add`/`update`/`delete`): `id` у update/delete, `status`,
  дубли `slug` в пределах типа, `;` без пробела в терминах, да/нет в
  `BOOL_FIELDS_DEFAULT_TRUE`, существование значений-путей.
- Записи `skip`/без action на сайт не уходят: их проблемы — предупреждения.
  Отброшенные `--filter`/`--skip-tax` не проверяются вовсе.

Новое поле, которое понимает маппинг, — добавить в `KNOWN_FIELDS`, иначе
проверка начнёт блокировать sync.

### Выбор набора данных: `--sheets`

Один и тот же флаг для обоих источников: листы Excel или табы JSON через запятую.

- Excel: не указан → все content-листы (+ taxonomy, если нет `--skip-tax`).
- JSON: **обязателен**. Значения по умолчанию нет — рядом с контентом в файле
  лежат служебные табы (`options`, `company` из `{key,label,value}`), синхронизировать
  «всё» небезопасно. Без флага — ошибка (exit 2) со списком табов файла.
  Несколько табов за прогон разрешены: обрабатываются по очереди, каждый со
  своим writer'ом (файл перечитывается перед табом и сохраняется после — в том
  числе на пути ошибки, через `finally`: иначе id уже созданных в WordPress
  записей пропали бы и повтор создал бы их заново).
- Выбор наборов проверяется **до** загрузки credentials: «какие табы есть в
  файле» — вопрос к файлу, ответ не должен требовать доступа к сайту.
- Пустой разбор (`--sheets ","`) — ошибка аргументов, а не «нечего делать, всё ок».

Служебный таб, выбранный по ошибке, валится на валидации (`type`/`taxonomy` не
заполнены) до подключения к сайту. Отдельной синхронизации настроек из JSON нет.

### Фильтр записей: `--filter field=value`

Повторяемый, условия объединяются по И, применяется после чтения записи и до
маппинга — одинаково для Excel и JSON. Сравнение точное строковое по сырому
значению поля. Dot-нотация — обычное имя поля (`--filter acf.f1=да`), понимать
вложенность payload не требуется. `type`/`taxonomy` берутся из дискриминатора
записи (в `Record.data` их нет). Отфильтрованные записи попадают в отчёт как
`skipped`. Реализация — `src/records/filters.py`, подключён к `sync`/`async`.

**`--dry-run` не открывает writer'ы — исправление реальной порчи файлов.**
До этого dry-run проходил полный путь обратной записи и заливал в файл проекта
заглушки dry-run-ответов: `id=0`, `action` у всех строк → `skip`, часть колонок
затиралась. То есть прогон «без изменений» молча портил и xlsx, и (после
подключения JSON) испортил бы json. Теперь `_writeback_enabled()` выключает
обратную запись и при `--dry-run`, и при `--no-update-excel`. Регрессия закрыта
тестами `test_dry_run_does_not_touch_the_file` (json, побайтово) и
`test_dry_run_does_not_touch_the_excel_file` (xlsx, по значениям ячеек).

**`--dry-run` больше не создаёт реальные термины таксономии.** Раньше
`_process_taxonomies` вызывал `get_or_create`/`get_or_create_cached` безусловно,
и превью «без изменений» на самом деле создавало на сайте недостающие термины
(включая предков иерархии `>` и мета-поле primary-термина). Теперь `dry_run`
пробрасывается до `TaxonomiesEndpoint.get_or_create`/`get_or_create_cached`:
поиск существующего термина выполняется как обычно (для точного превью), а
create-запрос — нет; отсутствующий термин возвращается плейсхолдером с ID=0
и не кладётся в кэш как настоящий. Тесты — `TestProcessTaxonomyStringDryRun`
(`tests/test_taxonomies.py`) и `TestTaxonomyDryRunDoesNotCreate`
(`tests/test_content_sync.py`).

### Прогресс: `[n/total]` в логе

По завершении каждой записи `sync`/`async` пишут в лог (INFO) строку
`[17/104] id=3425 updated` — `src/sync/progress.py`. Это контракт: antagor
читает поток лога и по нему показывает долю сделанного — снаружи номер записи
не вычислить. Не менять формат без поднятия версии.

- `total` — записи набора (лист/таб) после `--filter`/`--skip-tax`/`--test-mode`;
  на каждый набор свой счётчик.
- `skip`/пустой action засчитываются сразу одной строкой
  `[16/104] пропущено записей (skip/пустой action): 16`.
- Итог: `added`/`updated`/`deleted`/`error`. id — из ответа API, иначе из записи;
  нет ни того ни другого (ошибка `add`) — `id=- error (запись 7)`.
- В async номер — сколько сделано, а не позиция в файле; отметка ставится в
  момент завершения задачи (`BaseSync._tracked`), а не после `gather` пачки.
- Под `--json` лог идёт в stderr, под `--quiet` строки прогресса скрыты (уровень ERROR).

---

### Загрузка медиа: `media-upload`

`src/cli/media_commands.py`. Пути — аргументами или `--manifest`
(`[{path, alt?, title?, caption?}]`), ответ — id вложений. Команда **не знает
про файл данных**: не читает и не пишет Excel/JSON, id с записями
(`featured_media`) связывает вызывающий (antagor) — по имени файла, поэтому
`path` в ответе дословно тот, что пришёл (без `resolve()`). Не путать с
`img_path`: та колонка Excel-эпохи в JSON-реестр попасть не может.

- Путь как есть (абсолютный или от CWD), `resolve_media_file` не применяется:
  путь называет сам вызывающий.
- Частичный успех — `ok: true`, exit 0; ни одного загруженного — конверт
  ошибки с кодом причины (ошибка сайта важнее «файла нет на диске»).
- Отсутствующие файлы отсекаются до сайта; все отсутствуют — exit 2 без
  обращения к доступам.
- Файл и метаданные — два запроса. Упал второй → файл всё равно в `uploaded`
  с `meta_error`: вложение уже создано, объявить его незагруженным — дубль
  при повторе.
- Не дедуплицирует, не выводит alt/title сам, не гейтится (ничего не удаляет).
- Прогресс `[n/total] 3425.jpg -> id=4821` / `-> error: …` — формат как у sync.

---

## Обнаружение полей (FieldDetector)

Колонки Excel делятся на категории в таком порядке приоритета:

| Категория | Признак | Куда идёт |
| --- | --- | --- |
| system | action, id, img_path, content_path, image_url, alt_text, caption | не в API |
| taxonomy | slug из WP_TAXONOMIES / discovery / часть имени `cat`/`tax`/`term`/`tag` между `_`/`-` (не применяется к dot-нотации и `acf_`) | terms endpoint |
| acf | префикс `acf_` | ACF meta |
| nested | содержит `.` (dot-нотация) | `build_nested_payload()` |
| legacy seo | `seo_*` без точки | Yoast-стиль, устарело но работает |
| content | всё остальное | основной payload |

**Bool-поля с default=true:** `seo.robots.index`, `seo.robots.follow`, `seo.sitemap.include`.
Пустая ячейка → `true`. «нет»/«0»/«false» → `false`. Колонки нет вообще → поле не шлётся (preserve existing).

---

## Таксономии — с сайта

**Источник правды — сайт.** `sync`/`async` после подключения читают
`/wp/v2/taxonomies` и `/wp/v2/types` (`load_site_schema`, два GET) и кладут
имена в `config.sync.site_taxonomies`; распознавание колонок и листов идёт по
`config.known_taxonomies(config)` = `WP_TAXONOMIES` ∪ список сайта. Поэтому
план (`_open_plan`: `ExcelReader` классифицирует листы) составляется **после**
подключения — лист `rb_topic` таксономия только по данным сайта. Следить за
списком таксономий в `.env` не нужно: новые подхватываются сами.

Ошибка чтения схемы — ошибка прогона (exit 5), **без подмены** набором по
умолчанию: иначе `rb_topic` ушёл бы в payload обычным полем. (Старый
`WordPressDiscovery.discover_taxonomies` при сбое молча отдаёт
`categories/tags` — для схемы его не использовать.)

`validate`: есть доступы — та же схема сайта; сайт не ответил — exit 5 с
подсказкой `--offline` (тихо проверить хуже, чем обещано, нельзя). Нет доступов
или `--offline` — без сайта, `site_checked=false`, таксономии узнаются так:

1. `WP_TAXONOMIES=slug1,slug2` в `.env` — ручные исключения
2. Сам файл: записи-термины (`taxonomy`) и листы таксономий Excel — на все наборы
3. Стандартный набор (`category`, `post_tag`) + колонки/листы по соглашению:
   имя разбито `_`/`-` на части, и одна из частей — `cat` / `tax` / `term` / `tag` (`rb_services_cat`, `cat_services`, `region-term`; не `catalog_url`). Регистр при распознавании не важен, но проверка перед
   отправкой требует строчных. Правило одно — `constants.is_taxonomy_column`:
   по нему и распознаёт `FieldDetector`, и проверяет `preflight`, иначе
   проверка пропустила бы колонку, которую sync отправит как обычное поле.

`ExcelReader` умеет override имён листов: лист `categories` → таксономия `category`.

**Значение поля таксономии:** термины разделены `"; "`, внутри одного термина
уровни иерархии — `>`: `"термин1; термин2>термин2.1"`. Предки создаются слева
направо, записи присваивается последний уровень. `>` допустим только для
иерархических таксономий: если WP проигнорировал `parent` (таксономия плоская) —
`ValidationError`, а не тихие дубликаты терминов на каждом прогоне. Первый термин строки — основной
(primary), пишется в мету Yoast `_yoast_wpseo_primary_<taxonomy>` best-effort:
ошибка этой записи не валит синхронизацию.

---

## Retry и Circuit Breaker

```
CircuitBreaker: CLOSED → OPEN → HALF_OPEN
```

`asyncio.Lock` внутри — не threading. Порог и recovery через `WP_CIRCUIT_BREAKER_THRESHOLD` / `WP_CIRCUIT_BREAKER_RECOVERY`.

`@async_retry` — экспоненциальный backoff, configurable через `WP_MAX_RETRIES`, `WP_RETRY_DELAY`.
`TaxonomyCache` — тоже `asyncio.Lock`, заполняется лениво при первом обращении к термину.

---

## Reader — фильтрация мусора

`_drop_ignored_columns`: убирает `None`/пустые заголовки, `Unnamed:*` (pandas именует так пустые ячейки шапки), `__*` (служебные пометки).

`_drop_ignored_rows`: убирает (1) полностью пустые строки; (2) строки где **все** из `{action, title, name}` пусты.
Если ни одной из этих колонок в листе нет (например лист `options`) — правило не применяется.

---

## Конфигурация

`Settings(BaseSettings)` читает `.env` с префиксом `WP_`.
`AppConfig` агрегирует: `WordPressConfig`, `SyncConfig`, `PathsConfig`, `LoggingConfig`.

Ключевые переменные:

| Переменная | Назначение |
| --- | --- |
| `WP_URL` / `WP_USERNAME` / `WP_PASSWORD` | credentials |
| `WP_VERIFY_SSL=false` | отключить SSL (локальный Docker) |
| `WP_TAXONOMIES=cat,tag,service-cat` | override таксономий |
| `WP_SEO_FIELDS=seo.title,...` | dot-нотация SEO полей (и известные поля для проверки перед отправкой) |
| `WP_EXTRA_FIELDS=my_field,...` | свои поля проекта — проверка перед отправкой считает их известными |
| `WP_SYNC_CONCURRENCY` | параллельных запросов (async) |
| `WP_API_TIMEOUT` / `WP_MEDIA_TIMEOUT` | таймауты httpx |
| `WP_MAX_RETRIES` / `WP_RETRY_DELAY` | retry параметры |
| `WP_CIRCUIT_BREAKER_THRESHOLD` / `WP_CIRCUIT_BREAKER_RECOVERY` | CB настройки |
| `LLM_PROMPT` | промпт команды `llm` |
| `LLM_LEVEL` / `LLM_MODELS` | ТЕКСТ: уровень или цепочка моделей llmkit |
| `LLM_ENDPOINT` / `LLM_TOKEN` / `LLM_IMG_PROMPT` / `LLM_IMG_MODELS` | ТОЛЬКО картинки (`--column img_path`) |
| `WP_BULK_MIN_VERSION=1.2.0` | минимальная версия CLI для этого проекта |

### Команда `llm`: текст через llmkit, картинки — свои

Генерация **текста** идёт через общую библиотеку
[llmkit](https://github.com/bolandeu/llmkit) (`LLMClient.generate` → `LlmKit.generate`):
цепочки моделей с фолбэком, ретраи по типу ошибки, кэш и учёт стоимости — её,
а не наши. Ключи провайдеров llmkit берёт из **своего** конфига
(`~/Library/Application Support/llmkit/config.toml`) → переменной окружения →
связки ключей ОС; `.env` проекта на это не влияет. Расходы смотреть её же CLI:
`llm cost --by tool` (вызовы помечаются `tool=wp-bulk-editor`, `project=<имя>`).

- `--level`/`LLM_LEVEL` — именованный уровень из конфига llmkit (`cheap`/`judge`/…).
- `--model`/`LLM_MODELS` — явная цепочка `провайдер/модель` через запятую, перекрывает уровень.
- Не задано ни то, ни другое — берётся `[defaults] level` конфига llmkit.
- **`--endpoint`/`--token` на текст больше не действуют** (раньше — действовали).
  Нужен свой OpenAI-совместимый сервер для текста: провайдер
  `kind = "openai_compatible"` в конфиге llmkit, не флаг этого CLI.

Генерация **картинок** (`--column img_path`) в llmkit отсутствует, поэтому
осталась своим вызовом в `src/utils/llm_client.py::generate_image`: ей и только
ей нужны `--endpoint`/`--token` (`LLM_ENDPOINT`/`LLM_TOKEN`), модели из
`LLM_IMG_MODELS` и промпт из `LLM_IMG_PROMPT`.

---

## Система проектов

Проект (данные сайта: `.env`, Excel/JSON, `media/`, `content/`, `logs/`) живёт вне
этого репозитория — `--project`/`-P` (или `WP_PROJECT`) находит его директорию,
`resolve_project()` в `src/cli/project.py`, порядок разрешения:

1. **Явный путь** — `--project /path/to/project` (абсолютный или относительно cwd)
   указывает на существующую директорию → используется как есть. Побеждает
   безусловно, даже если ниже совпало бы legacy-имя.
2. **Legacy-имя** — `--project mysite`, не являющееся путём к существующей
   директории, резолвится в `<repo_root>/projects/mysite/` (для проектов, всё ещё
   физически лежащих внутри этого репозитория).
3. **cwd-walk** — `--project` не передан вовсе: подъём от текущей директории вверх
   в поисках `.env` (тот же маркер, что `ProjectContext.env_file`), не выше домашней
   директории пользователя и не глубже ~15 уровней.
4. **Глобальный режим** — ничего не найдено → `ProjectContext()` неактивен, команды
   работают без проекта (там, где это возможно).

Приоритет: CLI флаги > `.env` проекта > глобальный `.env`.

**Авто-обнаружение файла данных** — `sync`/`async` ищут xlsx **или** json в папке
проекта (`find_data_file()`, см. «Источники данных»), Excel-специфичные команды —
только xlsx (`find_excel()`). Явный `--file` всегда в приоритете и обязателен,
когда в папке лежат оба формата.

**Авто-обнаружение output** — `export` → `projects/<name>/exported_content/`, `export-media` → `projects/<name>/export_media/`.

**Минимальная версия CLI.** Проект объявляет её в своём `.env`:
`WP_BULK_MIN_VERSION=1.2.0`. Читает `ProjectContext.min_version`, проверяет
`check_cli_compat()` в callback приложения: версия старее — `CliTooOld` (exit 6)
до выполнения команды. Исключение — `doctor`: он обязан уметь диагностировать
именно несовместимую версию, поэтому показывает её проверкой `cli_compat`.
Запуск из исходников (пакет не установлен) версии не имеет — проверка пропускается.

**`cli_version` в каждом JSON-конверте** (успех и ошибка) — кладёт
`src/output/json.py`, не команды: вызывающий (antagor) пишет в журнал, чем сделан
результат. Версию читает `src/version.py` из метаданных пакета — у editable-установки
они фиксируются при установке, поэтому после нового тега переустановить tool. Что с какой версии (для `WP_BULK_MIN_VERSION` вызывающего) — в `CHANGELOG.md`.

Версия — это git-тег `vX.Y.Z` (строки `version =` в `pyproject.toml` нет), журнал
собирается из фрагментов `changelog.d/`. Поменялось поведение — фрагмент в том же
коммите (`uv run towncrier create +<slug>.<type>.md -c "…"`), релиз —
`uv run scripts/release.py patch|minor|major` (см. «Тесты и качество»).

---

## Диагностика и бандленная документация

`wp-bulk doctor [--json] [--check-connection]` — единственная команда проверки
готовности окружения. Проверки: `project`, `cli_compat`, `config`, `credentials`
(с источником: cli/env/keychain/.env), `credential_store`, и `connection` —
только по явному флагу, чтобы doctor оставался быстрым и офлайновым.
Статусы: `ok` / `warn` / `fail`; сам запуск диагностики всегда exit 0.

`src/data/SKILL.md` — соглашения CLI для агента, едет внутри пакета
(hatchling кладёт в wheel все файлы пакета `src`). Печатается флагом `wp-bulk --ai`.
**Списка команд там нет и быть не должно:** его печатает голый `wp-bulk` из
docstring'ов. Добавили команду — написали хороший docstring, SKILL.md не трогаем.

---

## Добавление новой команды

1. Выбрать нужный файл в `src/cli/` по теме или создать новый.
2. Навесить `@output.guard` под `@app.command()`, добавить `json_output: bool = output.JsonOption`
   и `quiet: bool = output.QuietOption`, результат отдавать через `output.emit_success(ctx, payload, render)`.
   (Ещё не переведённые команды используют `handle_command_errors(ctx)` из `common.py` — то же самое,
   но context manager'ом.) В docstring указать exit-коды.
3. Credentials грузить через `load_and_print_credentials(..., ctx=ctx)`.
4. Если нужен файл: `resolve_excel_file(ctx, file)` — разруливает explicit > project xlsx > None.
5. Если нужен `.env`: `resolve_env_file(ctx, env_file)`.
6. Если команда что-то спрашивает или необратимо меняет сайт — добавить
   `confirm: bool = output.ConfirmOption` и `non_interactive: bool = output.NonInteractiveOption`,
   вопрос задавать только через `output.require_confirmation`.
7. Импортировать модуль в `src/cli/__init__.py` — регистрация происходит автоматически через `@app.command()`.
8. Docstring с `Example: {cli} <команда> …`, `help=` у каждого параметра, группа — через
   `output.group("…")`; `uv run pytest --snapshot-update` и фрагмент `changelog.d/` (`added`).

---

## Исключения — иерархия

Каждое исключение несёт `code` и `exit_code` (контракт cli-builder), их печатает
`src/output/`. `CliError` — алиас `WPBulkEditorError`.

```
WPBulkEditorError                       UNEXPECTED (1)
├── ConfigurationError                  INVALID_ARGUMENTS (2)
├── ExcelError                          INVALID_ARGUMENTS (2)
│   ├── ExcelStructureError
│   ├── ExcelValidationError
│   └── ProfileSheetMissingError
├── RecordSourceError                   INVALID_ARGUMENTS (2)
├── WordPressError                      PROVIDER_ERROR (5)
│   ├── AuthenticationError   ← не ретраить   AUTH_REQUIRED (3)
│   ├── NotFoundError         ← не ретраить
│   ├── ServerError           ← ретраить
│   ├── NetworkError          ← ретраить
│   └── MediaUploadError
├── ValidationError                     INVALID_ARGUMENTS (2)
├── Aborted            ← отказ в confirm-гейте   ABORTED (1)
├── CliTooOld          ← WP_BULK_MIN_VERSION     CLI_TOO_OLD (6)
├── CredentialStoreUnavailable          CREDENTIAL_STORE_UNAVAILABLE (1)
├── ImageProcessingError                UNEXPECTED (1)
└── CircuitBreakerOpenError (utils/retry.py)   PROVIDER_ERROR (5)
```

---

## Соглашения

- Комментарии и логи — на **русском**.
- CLI help — на **русском**.
- Логировать через `loguru`, не `print()`.
- Credentials в логах: `username[:3]***`, только `path.name` для путей.
- Разделитель таксономий в Excel: `"; "` (точка с запятой + пробел).
- Path traversal: валидировать через `resolve().relative_to()` для всех пользовательских путей.
- Async: все WP API calls через `async with WordPressClient(...)`. `asyncio.run()` только на CLI-уровне.
- Кроссплатформенность: `pathlib`/`shutil`, никаких `os.system`, `shell=True` и
  POSIX-путей строкой. Пакет должен ставиться и работать на macOS/Linux/Windows.

---

## Тесты и качество

```bash
uv sync --all-groups
uv run scripts/release.py --check    # весь гейт — перед каждым коммитом
uv run pytest -q                     # покрытие включено (fail_under в pyproject)
uv run pytest --snapshot-update      # после правки справки команды
```

**Гейт — `scripts/release.py` (стандарт `tools/docs/standard-release.md` §3)**, копия
из cli-generator дословно, руками не править: `uv lock --check`, ruff check + format,
ty, pytest (покрытие, контракт справки, форма `auth status`), фрагмент changelog,
сборка wheel и `wp-bulk --version`/`--help` в чистом venv, osv-scanner по `uv.lock`.
ty в `tests/` для утиных дублёров только предупреждает (`[tool.ty.overrides]`),
`src/` — на полной строгости.

**Релиз:** `uv run scripts/release.py patch|minor|major` на чистом `main` —
гейт, `towncrier build`, коммит `Release vX.Y.Z` и annotated-тег; пушит пользователь
(`git push --follow-tags`). Строки `version =` нет, `CHANGELOG.md` руками не правится:
каждое изменение — фрагмент `changelog.d/+<slug>.<type>.md`.

**Справка команды = её docstring.** Поменял поведение команды — поправь docstring в том
же коммите (хуки `.claude/settings.json` и `.githooks/pre-commit` это проверяют,
`scripts/install_hooks.sh` — один раз на клон), строка `Example: {cli} <команда> …`
обязательна (`{cli}` подставляет `output.guard`; у команд без guard — буквально `wp-bulk`),
у каждого параметра `help=`; `tests/snapshots/help/` обновляет `--snapshot-update`.
Квадратные скобки в docstring экранировать `\\[…]` — иначе rich съест их как разметку.

**`auth status` (§8)** — `src/credentials/health.py`, форма зафиксирована
`tests/test_auth_status.py` (её смена — ломающее изменение). Цели `wp:<домен>`:
индекс сохранённых доменов в keychain (`credentials.manager.stored_names`, keyring
не умеет перечислять) + сайт активного проекта + `--provider`. LLM — отчёт llmkit.

Конвенции тестов: плоские `tests/test_<module>.py`, группировка в `class Test<Thing>:`,
`asyncio_mode = "auto"`. HTTP мокается через `httpx.MockTransport` + хелпер
`mock_transport(responses)` (`tests/test_wordpress_client.py`), настоящий OS keychain
в тестах подменяется (`tests/test_credentials.py::FakeKeyring`).

E2E не автоматизированы: нужен живой WordPress (docker-compose с `wordpress` + `mysql`,
Application Password в отдельном `.env.test`). Основной сценарий для ручной проверки —
`fetch` → правка Excel (`action=update/add/delete`) → `async` → сверка через WP API и
проверка, что новой строке записан `id`, а `action` стал `skip`.

---

## Windows

- Ставить через `uv tool install "git+<repo-url>"` — тогда `wp-bulk` попадает в PATH.
  При работе из клона (`uv pip install -e .`) `wp-bulk.exe` лежит в `.venv\Scripts\`,
  которая в PATH не добавляется: запускать `uv run wp-bulk ...` из папки проекта.
- `uv sync` ставит точно по `uv.lock` (воспроизводимо), `uv pip install -e .` — свежие
  совместимые версии по `pyproject.toml`.
- Кириллица «кракозябрами» в консоли: `chcp 65001` на сессию или `PYTHONUTF8=1` в
  переменных окружения системы.
- `uv` не найден сразу после установки — открыть терминал заново (PATH обновляется
  только в новой сессии).
- `SSL: CERTIFICATE_VERIFY_FAILED` на локальном Docker с самоподписанным сертификатом:
  `WP_VERIFY_SSL=false` в `.env` проекта.

## Каждое заметное изменение выходит релизом

Серверы, другие инструменты и Mac ставят только по тегам, не из `main`:
влитое без релиза изменение для них не существует. Изменение с фрагментом
changelog закончено только после релиза: влить в `main` →
`uv run scripts/release.py patch|minor|major` → попросить пользователя
сделать push с `--follow-tags` → назвать новый тег в отчёте. Служебные коммиты
(без фрагмента) ждут следующего релиза. Не оставлять фрагмент на `main` без
релиза, не назвав причину (`~/Projects/tools/docs/standard-release.md` §3).

## Инструмент в системе

Инструмент — один из сервисов `~/Projects/tools/`: корневой агент его
деплоит, хранит ключи (Doppler) и бэкапит данные (S3). Полные правила —
`~/Projects/tools/docs/standard-release.md` §13.

- **Сообщать корневому агенту** (в отчёте пользователю, рядом с тегом
  релиза): новая или переименованная переменная окружения / ключ, новая
  локальная база или новый путь к данным, новая или убранная зависимость от
  другого инструмента, обновлённый токен сохраняется локально, ломающий релиз.
- **Потребители**: перед ломающим изменением проверить, кто пользуется
  инструментом (корневой `AGENTS.md` → Service map), и назвать их в отчёте.
- **Серверы Linux без экрана**: сначала переменные окружения, keychain
  необязателен (его отсутствие при заданной переменной — не ошибка); имена
  переменных — уже существующие; без TTY никаких вопросов; без путей только для Mac.
- **Локальные данные** — в `platformdirs.user_data_dir("<tool>")`, не в
  репозитории; всё состояние — в файле базы, миграции делает сам инструмент;
  сам инструмент данные никуда не выгружает.
