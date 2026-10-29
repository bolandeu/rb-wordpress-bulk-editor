# Changelog

Пишется при `antagor publish` — руками не править (записи до v5.0.0 собраны из фрагментов towncrier).
Что с какой версии — ориентир для `WP_BULK_MIN_VERSION` вызывающего.

## v5.1.1 — 2026-10-29

### Outside epics

- Help: the whole credential chain, and where Doppler fits (WPBULK-002)

## v5.1.0 — 2026-10-24

### Outside epics

- rb-html-to-gutenberg from the Gitea registry, not github git (WPBULK-001)

## [5.0.0] - 2026-10-03

### Changed

- `bulk-delete` удаляет только с подтверждением: интерактивный вопрос или `--confirm`; под `--non-interactive` (а также в `--json` и без терминала) без `--confirm` — ошибка `INVALID_ARGUMENTS` (exit 2), к сайту не отправляется ни одного DELETE. `--dry-run` — превью без вопроса.

### Removed

- wp-bulk сужен до импорта и экспорта через REST API WordPress. Удалены команды `llm`, `counter`, `image-crop`, `image-compress`, `image-convert`, `reshape`, `vlook` — теперь «нет такой команды» (exit 2); из `describe --json` их тоже нет. `auth status` больше не показывает ключи LLM (`llmkit:<провайдер>`), `--provider llmkit` — неизвестный провайдер (exit 2); переменные `LLM_*` не читаются. Зависимости `rb-llmkit`, `Pillow`, `python-slugify` убраны.


## [4.2.2] - 2026-10-03

### Changed

- `post upsert` по id не меняет slug не-черновика (публикация, pending, future, private): живой URL сохраняется, slug из входа отбрасывается, ответ — `slug_kept: true` и текущий `slug`. У черновика slug обновляется. В ответе upsert всегда есть `slug` и `slug_kept`.


## [4.2.1] - 2026-10-03

### Added

- Гейт релиза проверяет контракт потребителя windmill → wp-bulk (`tests/contracts/windmill/wp-bulk.yaml`: `post upsert/get`, `media attach`, `describe`) офлайн на фальшивом WordPress; скрипты гейта синхронизированы с шаблоном cli-generator v1.8.1.


## [4.2.0] - 2026-10-03

### Added

- Команды WP-адаптера для Windmill: `post upsert` (JSON `--input <файл|->`: черновик по slug или обновление по id; seo через rb-seo, `featured`; ответ с `content_hash`), `post get --id`, `media attach --post` (`--path|--url|--media-id`), `describe --json` (контракт OpenCLI). Доступы — только окружение `WP_API_USER` / `WP_API_TOKEN`, флаги доступов отклоняются; коды ошибок `NOT_FOUND`, `CONFLICT`, `SEO_NOT_SUPPORTED` (exit 1), `AUTH_MISSING`, `AUTH_FAILED` (exit 3).

### Changed

- Без проекта логи команд пишутся в каталог логов пользователя (`platformdirs`, например `~/Library/Logs/wp-bulk`, `~/.local/state/wp-bulk/log`), а не в `./logs` текущей папки. С проектом — по-прежнему `<проект>/logs`.


## [4.1.0] - 2026-10-01

### Changed

- rb-llmkit 0.4 → 0.5: журнал расходов LLM — таблица `llmkit_calls` (была `calls`) в общем `usage.sqlite`, либо PostgreSQL через `LLMKIT_DATABASE_URL`. llmkit во всех инструментах одной машины поднимать одновременно.


## [4.0.0] - 2026-10-01

### Removed

- `--project <имя>` больше не ищет проект в `projects/` внутри репозитория wp-bulk: имя трактуется как путь относительно текущей папки, либо проект находится автопоиском `.env` вверх от cwd.

### Fixed

- `validate` больше не падает с `AttributeError` на листе с числовым заголовком колонки (например, `301`).


## [3.1.3] - 2026-09-30

### Changed

- Обновлены зависимости: rb-llmkit 0.4.3 (команда `llm decide`, провайдер anymodel, патч litellm) и rb-html-to-gutenberg 0.4.1 (мелкое исправление).


## [3.1.2] - 2026-09-30

### Fixed

- Если файл из `content_path` не найден или не прочитался, `content` в WordPress не отправляется вовсе — даже пустое поле `content` записи; контент на сайте остаётся прежним (в логе предупреждение). Пустой `content_path` контент не трогает.


## [3.1.1] - 2026-09-30

### Fixed

- Обратная запись после `sync`/`async` больше не очищает `content_path` — путь к файлу статьи остаётся в записи (JSON и Excel). `img_path` по-прежнему очищается, чтобы повторный прогон не загрузил картинку дублем.


## [3.1.0] - 2026-09-30

### Added

- `sync`/`async` отправляют заполненное поле `date` (дата публикации) при `add`/`update`: ISO `2026-01-12T17:16:41`, `2026-01-12 17:16`, `12.01.2026`; нераспознанная дата — ошибка проверки. `modified` WordPress REST не принимает — остаётся только для чтения.


## [3.0.0] - 2026-09-29

### Added

- `options` и `fetch-media` работают с JSON: формат выбирается по расширению `--file` (таб `options` с `{key, value}`, таб `media`). `options -f <проект>.json` отправляет на сайт таб реестра, `options --fetch` / `fetch-media` пишут таб в `wp-fetch/<таб>.json`. Непустой таб пересобирается только после подтверждения; в реестр проекта выгрузка не пишется. `fetch-media` получил `--json`, `--quiet`, `--confirm`, `--non-interactive`. В лист `options` Excel теперь пишется шапка `key, value` (старая `option, value` читается). Из записей `media` убрано поле `type`.


## [2.0.0] - 2026-09-27

### Added

- Группа `wp-bulk auth`: `auth status [--check] [--provider] --json` — отчёт о доступах в общей форме инструментов (стандарт §8): по записи `wp:<домен>` на каждый сайт из keychain и сайт активного проекта, ключи LLM — записями `llmkit:<провайдер>`; exit 3 при неисправном обязательном или неверном доступе. `auth login` / `auth logout` сохраняют и удаляют доступ сайта в OS keychain.

### Changed

- Дистрибутив переименован: `wp-bulk-editor` → `rb-wordpress-bulk-editor` (команда `wp-bulk` та же). На машинах со старым именем: `uv tool uninstall wp-bulk-editor`, затем `uv tool install "git+https://github.com/bolandeu/worppress-bulk-editor.git"`. Версия берётся из git-тега; `wp-bulk --version` печатает только номер (раньше — `wp-bulk-editor X.Y.Z — описание`); dev-сборка `0.0.0…` пропускает проверку `WP_BULK_MIN_VERSION`. Зависимости `rb-llmkit` и `rb-html-to-gutenberg` закреплены на тегах `v0.4.0`. Editable-установка под старым именем по-прежнему сообщает `cli_version` (запасное имя в `installed_version`).

### Fixed

- Поиск ACF-полей по первой записи типа/таксономии вызывал несуществующий параметр `get_all(per_page=1)` и всегда молча возвращал пустой список; теперь берётся первая страница через `list(per_page=1)`.


## [1.7.1] - 2026-09-26

- Загрузка медиа уходит с одним `Content-Type` — MIME файла (раньше вторым шёл `application/json` клиента, WordPress отвечал 400)

## [1.7.0] - 2026-09-26

- `media-upload`: файлы с диска → медиатека, ответ `uploaded [{path, id, source_url, meta_error?}]` / `failed`; частичный успех — exit 0

## [1.6.0] - 2026-09-26

- Таксономии и типы — с сайта при каждом прогоне (`WP_TAXONOMIES` не нужен); проверка сверяет колонки-таксономии, привязку к типу, `type`/`taxonomy` записей с сайтом; `validate --offline`, `site_checked`; `needs_env` убран из `taxonomies`

## [1.5.0] - 2026-09-26

- Прогресс `sync`/`async`: строка `[n/total] id=… added|updated|deleted|error` по завершении каждой записи

## [1.4.1] - 2026-09-26

- Служебные таксономии блочного редактора (`wp_pattern_category`, `wp_theme`, `wp_template_part_area`) скрыты по умолчанию в `taxonomies` и discovery

## [1.4.0] - 2026-09-26

- `taxonomies` отдаёт конверт (`taxonomies`, `env_line`, `needs_env` — имена не по соглашению); `bulk-delete` на `@output.guard`; google-команды и зависимости Google API удалены

## [1.3.0] - 2026-09-26

- `async` отдаёт тот же JSON-конверт, что `sync` (`--json`/`--quiet`, статистика, `log_file`); `--quiet` у `sync`/`async` глушит консольный лог

## [1.2.0] - 2026-09-26

- Проверка перед отправкой (`validate`, автоматически в `sync`/`async`, `--skip-checks`, `WP_EXTRA_FIELDS`); таксономия — по части имени `cat`/`tax`/`term`/`tag`

## [1.1.0] - 2026-09-26

- JSON-обмен: `update`/`fetch`/`validate` для JSON, `cli_version` в конверте
