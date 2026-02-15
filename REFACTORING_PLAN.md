# Refactoring Plan — ЗАВЕРШЁН

## CRITICAL (6/6)

- [x] 1. Division by zero — `image_processor.py`: проверка `orig_h == 0`
- [x] 2. Слишком широкий except — `llm_client.py`: `(KeyboardInterrupt, SystemExit)` пробрасываются
- [x] 3. Проглоченные исключения — `content_processor.py`: разделение на конкретные типы + `logger.exception`
- [x] 4. Race condition — `retry.py`: `is_half_open_probe` флаг захватывается внутри lock
- [x] 5. Path traversal — `content_processor.py`: запрет абсолютных путей, проверка `..`, `resolve().relative_to()`
- [x] 6. Path traversal — `content_exporter.py`: `safe_id` с санитизацией для имён файлов

## HIGH (11/11)

- [x] 7. Pagination break — `content_exporter.py`: `KeyboardInterrupt` пробрасывается, graceful return
- [x] 8. N+1 запросы — `content_exporter.py`: `_prefetch_media_cache()` batch загрузка через `?include=`
- [x] 9. Пустые строки credentials — `auth.py`: `.strip()` на domain/username/password
- [x] 10. glob_pattern traversal — `image_processor.py`: проверка `..`, `is_relative_to()`
- [x] 11. ~~Конфликт флагов~~ — проверено: в export_commands нет dry-run, конфликта нет
- [x] 12. Type hints — `html_to_gutenberg.py`: полные аннотации всех методов
- [x] 13. Unsafe dict access — `content_exporter.py`: `post.get("id")`

## MEDIUM (13/13)

- [x] 14. Credentials path в логах — `google_sheets.py`: логируется только имя файла
- [x] 15. Username в логах — `auth.py`: маскировка `user[:3]***`
- [x] 16. Hardcoded timeouts — `llm_client.py`: `image_timeout`, `connect_timeout` параметры; `content_exporter.py`: `download_timeout`
- [x] 17. Недостижимая ветка — `html_to_gutenberg.py`: рефакторинг `_convert_table`
- [x] 18. Regex — `llm_client.py`: скомпилирован на уровне модуля `_PLACEHOLDER_RE`
- [x] 19. Windows paths — `content_exporter.py`, `media_exporter.py`: `.as_posix()`
- [x] 20. ~~Missing .strip()~~ — проверено: google_commands уже имеет `.strip()`
- [x] 21-26. Форматирование ошибок — единый `handle_command_errors()` context manager

## Дублирование

- [x] Создан `src/cli/common.py`:
  - `setup_command_logging(ctx, name, verbose)`
  - `handle_command_errors()` — context manager
  - `load_and_print_credentials(...)`
  - `parse_comma_list(value)`
- [x] Рефакторинг: sync_commands, fetch_commands, export_commands, google_commands, tool_commands
