# Changelog

Собирается из фрагментов `changelog.d/` командой `scripts/release.py` — руками не править.
Что с какой версии — ориентир для `WP_BULK_MIN_VERSION` вызывающего.

<!-- towncrier release notes start -->

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
