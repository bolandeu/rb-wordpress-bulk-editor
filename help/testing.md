# Testing Plan — WordPress Bulk Editor

## Статус покрытия

Ядро покрыто хорошо: `excel/reader`, `excel/writer`, все маперы, `retry`, `wordpress_client`, `content_sync`.  
Критически не покрыты: весь `src/cli/`, `utils/llm_client`, `utils/image_processor`, `utils/content_exporter`, `utils/google_sheets`.

---

## Приоритет 1 — Критично (баги уже случались)

### `AppConfig.from_excel` → `WordPressConfig.from_credentials`

Регрессия: `AppConfig.from_excel` собирал `WordPressConfig(...)` напрямую, теряя `verify_ssl`.  
Тест уже есть (`test_propagates_verify_ssl_from_env`), но нужно покрыть все пути сборки конфига.

```python
# Что проверять:
# - from_excel с WP_VERIFY_SSL=false в env → client.verify == False
# - from_credentials с явным verify_ssl=False → то же
# - прямой конструктор WordPressConfig не должен использоваться нигде кроме from_credentials
```

### `ExcelWriter._dirty` — файл не трогается при read-only командах

Регрессия: `save()` перезаписывал файл даже без изменений, уничтожая форматирование.

```python
# Что проверять:
# - load() → save() без update_row → mtime не изменился
# - load() → update_row (колонка не найдена) → save() → mtime не изменился
# - load() → update_row (колонка найдена) → save() → файл обновлён
# - append_rows → _dirty == True
```

### `ExcelReader` — индекс не сбрасывается

Критично для writeback: оригинальный label строки должен совпасть с тем что ищет `ExcelWriter.update_row`.

```python
# Что проверять:
# - read_sheet() на файле с фильтрованными строками → индексы не consecutive от 0
# - эти же индексы переданы в ExcelWriter.update_row → запись в правильную строку
```

---

## Приоритет 2 — CLI-слой (весь не покрыт)

### `ProjectContext` / `resolve_project`

```python
# Что проверять:
# - resolve_project(None) → inactive context, нет .env нагрузки
# - resolve_project("mysite") → загружает projects/mysite/.env
# - resolve_project("nonexistent") → FileNotFoundError
# - find_excel() → возвращает файл с именем проекта если есть, иначе любой .xlsx, иначе None
# - resolve_output() → пишет в папку проекта
```

### `resolve_excel_file` / `resolve_env_file` (helpers.py)

```python
# Что проверять:
# - явный --file всегда выигрывает у project auto-detect
# - без --file и без проекта → None (не падает)
# - команда options без --file при активном проекте → берёт xlsx из проекта
```

### `handle_command_errors()`

```python
# Что проверять:
# - WordPressError → печатает сообщение, exit code 1
# - AuthenticationError → отдельное сообщение про credentials
# - ConfigurationError → понятный вывод
# - неожиданное исключение → не роняет трейсбэк в продакшен, логирует
```

### `_validate_test_mode` (sync_commands.py)

```python
# Что проверять:
# - test_mode=True без --sheets → ошибка "требует один лист"
# - test_mode=True с sheets="posts,pages" (два листа) → ошибка
# - test_mode=True с sheets="posts" → OK, обрабатывает только 3 строки
```

---

## Приоритет 3 — Утилиты

### `LLMClient`

```python
# Что проверять (с моком httpx):
# - replace_placeholders("[title] и [excerpt]", {...}) → подстановка
# - replace_placeholders с отсутствующим ключом → оставляет как есть или пустая строка
# - fallback между моделями: первая бросает → пробует вторую
# - retry на 429/500 → exponential backoff
# - strip_html_tags, markdown_to_html, html_to_markdown — юнит-тесты без httpx
# - streaming response — корректная сборка чанков
```

### `ImageProcessor`

```python
# Что проверять (с реальными тестовыми PNG):
# - parse_aspect_ratio("16:9") → (16, 9)
# - parse_aspect_ratio("invalid") → ValueError
# - crop_image с aspect_ratio=1:1, strategy=center → квадрат
# - crop_image с aspect_ratio=16:9, strategy=top → привязка к верху
# - compress_image → output меньше input
# - convert_image(jpg → webp) → файл .webp создан
# - RGBA → RGB конвертация при сохранении в JPEG (белый фон)
# - EXIF auto-orient → повёрнутые фото выпрямляются
```

### `WordPressDiscovery`

```python
# Что проверять (с моком WordPressClient):
# - discover_post_types() → фильтрует служебные типы (attachment, revision, nav_menu_item…)
# - discover_taxonomies() → возвращает slugs
# - discover_acf_fields_for_post_type() → пустой список если ACF нет
# - analyze_fields_from_data() → вытаскивает ключи из вложенных объектов
```

---

## End-to-End тесты

E2E требуют реального WordPress. Запускать через Docker с WP + MySQL.

### Инфраструктура

```yaml
# docker-compose.test.yml (набросок)
services:
  db:
    image: mysql:8
    environment:
      MYSQL_DATABASE: wordpress
      MYSQL_USER: wp
      MYSQL_PASSWORD: wp
      MYSQL_ROOT_PASSWORD: root
  wordpress:
    image: wordpress:latest
    ports: ["8080:80"]
    environment:
      WORDPRESS_DB_HOST: db
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wordpress
```

Настройка: установить WP, создать Application Password, прописать в `.env.test`.

### E2E сценарии

**Сценарий 1 — fetch → edit → sync (основной цикл)**

```
1. fetch → создаёт fetch.xlsx с записями из WP (action=skip)
2. Изменить несколько строк: action=update, поменять title/excerpt
3. Добавить новую строку: action=add
4. sync → отправить изменения
5. Проверить через WP API: обновлённые записи содержат новые данные
6. Проверить Excel: новой строке присвоен id, action=skip
```

**Сценарий 2 — async с retry (сеть)**

```
1. Запустить async против WP
2. Убедиться что при временной ошибке (502) происходит retry
3. Убедиться что circuit breaker открывается при N ошибках подряд
```

**Сценарий 3 — медиа: upload → update alt_text → delete**

```
1. sync --sheets media, action=add, img_path=test.jpg → файл загружен, id получен
2. Изменить alt_text в Excel, action=update → alt_text обновлён в WP
3. action=delete → файл удалён из библиотеки
4. Проверить что action=delete без --delete-media НЕ удаляет медиа у записей
```

**Сценарий 4 — таксономии**

```
1. fetch → лист categories содержит термины
2. Добавить строки в лист categories: action=add с name
3. sync → термины созданы в WP
4. В листе posts: action=update, categories="Новая категория"
5. sync → запись привязана к новому термину
```

**Сценарий 5 — SEO / dot-нотация**

```
1. fetch → колонки seo.title, seo.description присутствуют
2. Изменить seo.title, action=update
3. sync → Yoast/SEO плагин содержит новое значение
4. fetch повторно → значение вернулось из API
```

**Сценарий 6 — options**

```
1. options --fetch → options.xlsx создан, содержит site_title
2. Изменить значение site_title в Excel
3. options --file options.xlsx → WordPress обновил настройку
4. options --fetch снова → новое значение в файле
```

**Сценарий 7 — bulk-delete + dry-run**

```
1. Создать несколько черновиков через WP API
2. bulk-delete --post-type posts --status draft --dry-run → показывает список, ничего не удаляет
3. bulk-delete --post-type posts --status draft → удаляет (в корзину)
4. bulk-delete --force → полное удаление, записей нет даже в корзине
```

**Сценарий 8 — --columns (частичное обновление)**

```
1. Запись в WP: title="A", excerpt="B", status="publish"
2. Excel: action=update, title="A-new", excerpt="B-new"
3. sync --columns excerpt → только excerpt обновился, title не тронут
4. Проверить через API
```

---

## Что НЕ нужно тестировать автоматически

- Google Sheets команды — требуют OAuth, нужен stub сервис или ручной тест
- LLM image generation — зависит от внешнего API (gemini), слишком дорогой
- image-crop визуальные результаты — достаточно проверить размер/формат выходного файла
