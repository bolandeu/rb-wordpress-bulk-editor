# WordPress Bulk Editor

Python CLI-инструмент для синхронизации данных между Excel файлами и WordPress через REST API.

## Возможности

- ✅ Синхронизация постов, страниц, кастомных типов контента
- ✅ Работа с таксономиями (категории, теги, кастомные)
- ✅ Поддержка ACF полей
- ✅ Загрузка медиафайлов (локальные и по URL)
- ✅ SEO поля (Yoast/Rank Math)
- ✅ Множественные листы Excel
- ✅ Умная работа с таксономиями (автосоздание)
- ✅ Обратная запись ID в Excel

## Установка

```bash
# Клонировать репозиторий
git clone <repo-url>
cd wordpress-bulk-editor

# Установить зависимости
pip install -r requirements.txt
```

## Структура Excel файла

### Лист `profile` (обязательно)

| DOMAIN | API_USER | API_TOKEN |
|--------|----------|-----------|
| https://example.com | admin | xxxx xxxx xxxx xxxx |

### Листы контента (posts, pages, custom)

**Обязательные колонки:**
- `action` - add, update, delete, skip
- `id` - WordPress ID (для update/delete)

**Медиафайлы:**
- `img` - путь к медиа для загрузки
- `featured_media` - ID (заполняется автоматически)
- `image_url` - ссылка (заполняется автоматически)

**Таксономии:**
- `categories`, `tags` - названия через `; `
- `service-cat` - кастомные таксономии и любые поля гед содержится `-cat`

**ACF поля:**
- `acf_h1`, `acf_field_name` - ACF поля

**Любые другие колонки** автоматически передаются в WordPress API.

## Использование

### Валидация файла

```bash
python -m src.cli validate --file example.xlsx
```

### Синхронизация Excel → WordPress

```bash
# Полная синхронизация
python -m src.cli sync --file example.xlsx

# Только определенные листы
python -m src.cli sync --file example.xlsx --sheets posts,pages

# Dry-run (без изменений)
python -m src.cli sync --file example.xlsx --dry-run

# С подробными логами
python -m src.cli sync --file example.xlsx --verbose
```

**Опции sync:**
- `--file`, `-f` - путь к Excel файлу (обязательно)
- `--sheets` - список листов через запятую
- `--dry-run`, `-d` - режим симуляции
- `--verbose`, `-v` - подробные логи
- `--concurrency` - количество параллельных запросов (default: 5)
- `--skip-media` - пропустить загрузку медиа
- `--no-update-excel` - не записывать ID обратно

### Получение данных WordPress → Excel

```bash
# Получить все опубликованные посты
python -m src.cli fetch --file example.xlsx --status publish

# Получить все данные (любой статус)
python -m src.cli fetch --file example.xlsx

# Только определенные листы
python -m src.cli fetch --file example.xlsx --sheets posts,pages

# Ограничить количество записей
python -m src.cli fetch --file example.xlsx --limit 50
```

**Опции fetch:**
- `--file`, `-f` - путь к Excel файлу (обязательно)
- `--sheets` - список листов для заполнения
- `--status` - фильтр по статусу (publish, draft, any) (default: any)
- `--limit` - максимум записей на лист

### Обновление настроек сайта

Создайте лист `options` в Excel:

| option | value |
|--------|-------|
| site_title | My Website |
| posts_per_page | 10 |
| timezone | Europe/Moscow |

Затем выполните:

```bash
# Обновить настройки
python -m src.cli options --file example.xlsx

# Показать что будет отправлено (без отправки)
python -m src.cli options --file example.xlsx --dry-run
```

**Опции options:**
- `--file`, `-f` - путь к Excel файлу (обязательно)
- `--dry-run`, `-d` - показать без отправки

### Экспорт контента сайта в файлы

Экспортирует весь контент сайта (posts, pages, custom types) в HTML файлы, организованные по папкам. Создает CSV индекс всех записей.

```bash
# Экспорт с credentials из Excel файла
python -m src.cli export --file example.xlsx

# Экспорт с явными credentials
python -m src.cli export --domain https://example.com --username admin --password "xxxx xxxx xxxx xxxx"

# Указать папку для экспорта
python -m src.cli export --file example.xlsx --output my_export

# С подробными логами
python -m src.cli export --file example.xlsx --verbose
```

**Опции export:**
- `--file`, `-f` - Excel файл с профилем (альтернатива domain/username/password)
- `--domain`, `-d` - URL сайта WordPress
- `--username`, `-u` - имя пользователя
- `--password`, `-p` - Application Password
- `--output`, `-o` - папка для экспорта (default: exported_content)
- `--verbose`, `-v` - подробные логи

**Результат экспорта:**
```
exported_content/
├── posts/
│   ├── 123.html
│   ├── 124.html
│   └── ...
├── pages/
│   ├── 10.html
│   └── 11.html
├── service/          # Кастомный тип контента
│   └── 50.html
└── index.csv         # Индекс всех записей
```

**Формат index.csv:**
```csv
post_type,id,title,file_path,seo_title,status,date
posts,123,Article Title,posts/123.html,SEO Title,publish,2024-01-10T12:00:00
pages,10,About Us,pages/10.html,,publish,2024-01-05T10:30:00
```

## Логи

Логи сохраняются в папке `logs/`:
- `sync_YYYYMMDD_HHMMSS.log` - текстовый лог
- `sync_YYYYMMDD_HHMMSS.json` - JSON статистика

## Структура проекта

```
wordpress-bulk-editor/
├── .env.example              # Пример конфигурации
├── README.md
├── requirements.txt
├── data/                     # Excel файлы и медиа (не коммитится)
│   ├── example.xlsx
│   └── media/
├── logs/                     # Логи синхронизации (генерируются автоматически)
└── src/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py                # CLI интерфейс
    ├── config.py             # Конфигурация
    ├── constants.py
    ├── exceptions.py
    ├── excel/                # Работа с Excel
    │   ├── profile.py
    │   ├── reader.py
    │   └── writer.py
    ├── wordpress/            # WordPress REST API
    │   ├── client.py
    │   ├── posts.py
    │   ├── taxonomies.py
    │   └── media.py
    ├── sync/                 # Синхронизация
    │   └── content_sync.py
    ├── mappers/              # Преобразование данных
    │   ├── content_mapper.py
    │   └── field_detector.py
    └── utils/
        └── logger.py
```

## Детальная структура Excel файла

### Служебные колонки

Эти колонки обрабатываются скриптом и НЕ передаются напрямую в WordPress API:

- `action` - **обязательно**: тип операции
  - `add` - создать новую запись
  - `update` - обновить существующую
  - `delete` - удалить (в корзину)
  - `content` - обновить только контент из файла (markdown/html)
  - `skip` или пустое - пропустить строку

- `id` - WordPress ID
  - Пустое для `action=add`
  - Обязательно для `update` и `delete`
  - Автоматически заполняется после создания

- `type` - тип контента (опционально, дублирует имя листа)

- `img` - путь к медиафайлу для загрузки
  - Локальный путь: `media/image.jpg` (относительно папки проекта)
  - URL: `https://example.com/image.jpg`

- `featured_media` - ID загруженного изображения (заполняется автоматически)

- `image_url` - абсолютная ссылка на изображение (заполняется автоматически из API)

- `link` - URL записи в WordPress (заполняется автоматически)

- `date` - дата создания (заполняется автоматически)

- `modified` - дата изменения (заполняется автоматически)

- `content_path` - путь к файлу контента (автоматически обрабатывается при `action=add` или `action=update`)
  - Путь относительно корня проекта или `data/content/`
  - Поддерживаются форматы: `.md`, `.markdown`, `.html`, `.htm`
  - Markdown автоматически конвертируется в HTML
  - HTML преобразуется в Gutenberg блоки
  - Если заполнено - контент из файла загружается при создании/обновлении поста
  - Пример: `content_path: data/content/article-123.md` или `export/posts/123.html`

### Стандартные поля контента

Эти поля передаются в WordPress API:

- `title` - заголовок (обязательно для `add`)
- `content` - содержимое
- `excerpt` - краткое описание
- `status` - статус публикации
  - `publish` - опубликовано
  - `draft` - черновик
  - `pending` - на модерации
  - `private` - приватное
- `slug` - URL slug
- `parent` - ID родительской записи (для страниц)

### Таксономии

Названия терминов через разделитель `; ` (точка с запятой + пробел):

- `categories` - категории: `Новости; Технологии`
- `tags` - теги: `wordpress; api; sync`
- `{название}-cat` - кастомные таксономии: `service-cat`

Если термин не существует, он будет создан автоматически.

### ACF поля

Колонки с префиксом `acf_` автоматически группируются в блок `acf`:

- `acf_h1` → `acf: { h1: "значение" }`
- `acf_event_date` → `acf: { event_date: "2024-12-31" }`
- `acf_custom_field` → `acf: { custom_field: "значение" }`

### SEO и кастомные поля

Все остальные колонки автоматически передаются в WordPress API:

- `seo_title`, `seo_description` - SEO поля
- `seo_meta_keywords`, `seo_noindex`, `seo_nofollow`
- `custom_permalink`
- Любые другие поля, которые обрабатывает ваш REST API

## Примеры использования

### Пример 1: Создание новых постов

Excel лист `posts`:

| action | id | title | content | status | categories | img_path |
|--------|-------|-------|---------|--------|------------|----------|
| add | | Новый пост | Содержимое... | publish | Новости | media/photo.jpg |
| add | | Еще пост | Текст... | draft | Технологии; Новости | https://example.com/image.jpg |

```bash
python -m src.cli sync --file data/posts.xlsx
```

Результат:
- Созданы 2 поста
- Категории "Новости" и "Технологии" созданы автоматически (если не существовали)
- Изображения загружены и установлены как featured media
- Колонка `id` заполнена WordPress ID
- Колонки `featured_media`, `image_url`, `link` заполнены

### Пример 2: Обновление существующих постов

| action | id | title | status |
|--------|-----|-------|--------|
| update | 123 | Обновленный заголовок | publish |
| update | 456 | Другой заголовок | draft |

```bash
python -m src.cli sync --file data/posts.xlsx --sheets posts
```

### Пример 3: Работа с ACF полями

| action | id | title | acf_h1 | acf_price | acf_event_date |
|--------|-------|-------|--------|-----------|----------------|
| add | | Событие | Заголовок H1 | 1000 | 2024-12-31 |

API payload:
```json
{
  "title": "Событие",
  "acf": {
    "h1": "Заголовок H1",
    "price": 1000,
    "event_date": "2024-12-31"
  }
}
```

### Пример 4: Загрузка контента и изображений из файлов

Вы можете автоматически загружать контент и изображения при создании или обновлении постов:

**1. Создайте файлы контента:**

`export/posts/123.md`:
```markdown
# Основной заголовок

Это абзац с текстом.

## Подзаголовок

- Пункт 1
- Пункт 2

**Жирный текст** и *курсив*.
```

**2. В Excel укажите пути к файлам:**

| action | id | title | content_path | img_path |
|--------|-----|-------|--------------|----------|
| add | | Новый пост | export/posts/123.md | export/posts/123.jpg |
| update | 456 | | export/posts/456.html | export/posts/456.jpg |

```bash
python -m src.cli sync --file data/posts.xlsx --sheets posts
```

**Результат:**
- Если `content_path` заполнен - контент загружается из файла при add/update
- Если `img_path` заполнен - изображение загружается при add/update (даже если featured_media уже есть)
- Markdown конвертируется в HTML и преобразуется в Gutenberg блоки
- Пути могут быть относительно корня проекта (`export/tzm/123.jpg`) или `data/content/`
- Поддерживаются форматы контента: `.md`, `.markdown`, `.html`, `.htm`

### Пример 5: Dry-run режим (тестирование без изменений)

```bash
python -m src.cli sync --file data/posts.xlsx --dry-run --verbose
```

Показывает что будет сделано, но не выполняет изменения.

### Пример 6: Обработка только определенных листов

```bash
python -m src.cli sync --file data/example.xlsx --sheets posts,pages
```

Обрабатывает только листы `posts` и `pages`, остальные пропускаются.

## Логирование

### Структура логов

Каждая синхронизация создает два файла в папке `logs/`:

1. **Текстовый лог** (`sync_20240110_143022.log`):
```
14:30:22 | INFO     | Чтение credentials из листа 'profile'...
14:30:22 | INFO     | WordPress: https://example.com
14:30:23 | INFO     | Обработка листа: posts
14:30:25 | INFO     | Создан пост ID=123 | Title: "Новый пост"
14:30:27 | WARNING  | Пропущена строка 5 (action=skip)
14:30:30 | ERROR    | Ошибка обновления поста ID=456: Not found
```

2. **JSON статистика** (`sync_20240110_143022.json`):
```json
{
  "file": "example.xlsx",
  "started_at": "2024-01-10T14:30:22",
  "finished_at": "2024-01-10T14:32:15",
  "duration_seconds": 113,
  "sheets": {
    "posts": {
      "total": 50,
      "added": 20,
      "updated": 15,
      "deleted": 5,
      "skipped": 8,
      "errors": 2
    }
  },
  "overall": {
    "total": 50,
    "successful": 40,
    "errors": 2,
    "success_rate": 96.67
  }
}
```

### Уровни логирования

- `INFO` (по умолчанию) - основные операции
- `DEBUG` (с флагом `--verbose`) - детальная информация, HTTP запросы

## Troubleshooting

### Ошибка "401 Unauthorized"

**Проблема**: Неверные credentials или неправильный формат Application Password.

**Решение**:
1. Проверьте лист `profile` в Excel
2. Убедитесь что Application Password в формате: `xxxx xxxx xxxx xxxx`
3. Проверьте что пользователь имеет права администратора

### Ошибка "Connection refused"

**Проблема**: WordPress недоступен или неверный DOMAIN.

**Решение**:
1. Проверьте что сайт доступен в браузере
2. Убедитесь что DOMAIN включает `https://` или `http://`
3. Проверьте что REST API включен: `https://yoursite.com/wp-json/`

### Таксономии не создаются

**Проблема**: Таксономия не зарегистрирована или недоступна через REST API.

**Решение**:
1. Убедитесь что таксономия зарегистрирована с `'show_in_rest' => true`
2. Проверьте endpoint в браузере: `https://yoursite.com/wp-json/wp/v2/your-taxonomy`

### Медиафайлы не загружаются

**Проблема**: Файл не найден или превышен размер.

**Решение**:
1. Проверьте путь к файлу (относительно корня проекта)
2. Убедитесь что файл существует
3. Проверьте размер файла (по умолчанию max 10 MB)
4. Для URL: убедитесь что ссылка доступна

### Unicode ошибки в консоли (Windows)

**Проблема**: Windows консоль не поддерживает UTF-8.

**Решение**:
```bash
# Установите кодировку UTF-8 перед запуском
chcp 65001
python -m src.cli sync --file example.xlsx
```

## Требования

- Python 3.10+
- WordPress с REST API
- Application Password для аутентификации
- Права администратора WordPress

## Лицензия

MIT
