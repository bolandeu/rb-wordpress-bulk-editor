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

---

## Система проектов

`--project mysite` / `-P mysite` → `projects/mysite/` содержит `.env`, Excel, `media/`, `content/`, `logs/`.
Приоритет: CLI флаги > `.env` проекта > глобальный `.env`.

**Авто-обнаружение Excel** — только для `fetch`. Остальные команды (`sync`, `async`, `llm`, `validate`, `fetch-media`) требуют явный `--file`.

**Авто-обнаружение output** — `export` → `projects/<name>/exported_content/`, `export-media` → `projects/<name>/export_media/`.

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
6. Импортировать модуль в `src/cli/__init__.py` — регистрация происходит автоматически через `@app.command()`.

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
