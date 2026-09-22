# AGENTS.md — WordPress Bulk Editor

Bidirectional CLI: Excel ↔ WordPress. Async-first, project-aware, опциональный Pillow.

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
`bulk-delete` сначала даёт `--dry-run` (превью, без вопросов и без изменений),
затем `output.require_confirmation(ctx, описание, confirm)` — и только потом удаляет.
Свой `typer.confirm` в командах не писать: под `--non-interactive`, в `--json`
и когда stdin не терминал, вопрос должен превращаться в `INVALID_ARGUMENTS` (exit 2),
а не в зависший prompt. Гейт вызывается **после** проверки `--dry-run`, не до.

**Секреты — keychain или окружение, не файлы проекта.**
`src/credentials/manager.py` (keyring, provider = домен сайта).
Порядок: CLI-флаг → переменная окружения → keychain → `.env` (legacy).

**pandas 2.x StringDtype.**
При записи в колонку с `StringDtype` (`is_string_dtype()`) значение должно быть `str`.
Иначе pandas бросает TypeError. `_coerce_for_column()` в `writer.py` делает каст.

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
```

---

## Обнаружение полей (FieldDetector)

Колонки Excel делятся на категории в таком порядке приоритета:

| Категория | Признак | Куда идёт |
| --- | --- | --- |
| system | action, id, img_path, content_path, image_url, alt_text, caption | не в API |
| taxonomy | slug из WP_TAXONOMIES / discovery / суффикс `_cat -cat _tag -tag _tax -tax` (суффикс не применяется к dot-нотации и `acf_`) | terms endpoint |
| acf | префикс `acf_` | ACF meta |
| nested | содержит `.` (dot-нотация) | `build_nested_payload()` |
| legacy seo | `seo_*` без точки | Yoast-стиль, устарело но работает |
| content | всё остальное | основной payload |

**Bool-поля с default=true:** `seo.robots.index`, `seo.robots.follow`, `seo.sitemap.include`.
Пустая ячейка → `true`. «нет»/«0»/«false» → `false`. Колонки нет вообще → поле не шлётся (preserve existing).

---

## Таксономии — трёхуровневый fallback

1. `WP_TAXONOMIES=slug1,slug2` в `.env` — абсолютный приоритет (override)
2. `WordPressDiscovery` — опрос API сайта
3. Стандартный набор (`category`, `post_tag`) + колонки/листы с суффиксом
   `_cat -cat _tag -tag _tax -tax` (регистронезависимо)

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
| `WP_SEO_FIELDS=seo.title,...` | dot-нотация SEO полей |
| `WP_SYNC_CONCURRENCY` | параллельных запросов (async) |
| `WP_API_TIMEOUT` / `WP_MEDIA_TIMEOUT` | таймауты httpx |
| `WP_MAX_RETRIES` / `WP_RETRY_DELAY` | retry параметры |
| `WP_CIRCUIT_BREAKER_THRESHOLD` / `WP_CIRCUIT_BREAKER_RECOVERY` | CB настройки |
| `LLM_ENDPOINT` / `LLM_TOKEN` / `LLM_MODELS` / `LLM_PROMPT` | LLM генерация |
| `GOOGLE_SHEET_ID` / `GOOGLE_SHEET_CREDENTIALS_PATH` | Google Sheets |
| `WP_BULK_MIN_VERSION=1.2.0` | минимальная версия CLI для этого проекта |

---

## Система проектов

`--project mysite` / `-P mysite` → `projects/mysite/` содержит `.env`, Excel, `media/`, `content/`, `logs/`.
Приоритет: CLI флаги > `.env` проекта > глобальный `.env`.

**Авто-обнаружение Excel** — только для `fetch`. Остальные команды (`sync`, `async`, `llm`, `validate`, `fetch-media`) требуют явный `--file`.

**Авто-обнаружение output** — `export` → `projects/<name>/exported_content/`, `export-media` → `projects/<name>/export_media/`.

**Минимальная версия CLI.** Проект объявляет её в своём `.env`:
`WP_BULK_MIN_VERSION=1.2.0`. Читает `ProjectContext.min_version`, проверяет
`check_cli_compat()` в callback приложения: версия старее — `CliTooOld` (exit 6)
до выполнения команды. Исключение — `doctor`: он обязан уметь диагностировать
именно несовместимую версию, поэтому показывает её проверкой `cli_compat`.
Запуск из исходников (пакет не установлен) версии не имеет — проверка пропускается.

---

## Диагностика и бандленная документация

`wp-bulk doctor [--json] [--check-connection]` — единственная команда проверки
готовности окружения. Проверки: `project`, `cli_compat`, `config`, `credentials`
(с источником: cli/env/keychain/.env), `credential_store`, и `connection` —
только по явному флагу, чтобы doctor оставался быстрым и офлайновым.
Статусы: `ok` / `warn` / `fail`; сам запуск диагностики всегда exit 0.

`src/data/SKILL.md` — соглашения CLI для агента, едет внутри пакета
(`[tool.setuptools.package-data]`). Печатается флагом `wp-bulk --ai`.
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
uv sync
uv run python -m pytest -q                     # (uv run pytest не резолвится в PATH)
uv run python -m pytest -q --cov=src --cov-report=term-missing   # fail_under в pyproject
uv run ruff check .
```

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
