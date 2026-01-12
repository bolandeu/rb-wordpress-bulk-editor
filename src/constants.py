"""
Константы для WordPress Bulk Editor
"""

# Служебные колонки (не передаются в API)
SYSTEM_COLUMNS = {
    "action",
    "id",
    "type",
    "img_path",  # Путь к изображению (обрабатывается отдельно)
    "content_path",  # Путь к файлу контента (обрабатывается отдельно)
    "link",
    "date",
    "modified",
    "image_url",
    "featured_media",  # Передается, но заполняется автоматически
}

# Валидные значения для action
VALID_ACTIONS = {"add", "update", "delete", "skip"}

# Названия служебных листов
SYSTEM_SHEETS = {"profile", "options", "media"}

# Стандартные таксономии WordPress
STANDARD_TAXONOMIES = {"categories", "tags", "category", "tag"}

# Разделитель таксономий в Excel
TAXONOMY_DELIMITER = "; "

# ACF префикс
ACF_PREFIX = "acf_"

# Таксономия суффикс
TAXONOMY_SUFFIX = "-cat"

# WordPress REST API базовый path
WP_API_BASE = "/wp-json/wp/v2"

# Timeout для HTTP запросов (секунды)
DEFAULT_TIMEOUT = 30

# Максимум параллельных запросов
DEFAULT_CONCURRENCY = 5

# Размер батча
DEFAULT_BATCH_SIZE = 10

# Максимум повторных попыток
DEFAULT_MAX_RETRIES = 3

# Лимит записей на лист при fetch
DEFAULT_FETCH_LIMIT = 100

# Формат даты для логов
LOG_DATE_FORMAT = "%Y%m%d_%H%M%S"
