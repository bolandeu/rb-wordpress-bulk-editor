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
SYSTEM_SHEETS = {"options", "media"}

# Стандартные таксономии WordPress
STANDARD_TAXONOMIES = {"categories", "tags", "category", "tag"}

# Паттерны для фильтрации служебных типов записей (по умолчанию)
DEFAULT_SKIP_POST_TYPE_PATTERNS = ["wp_", "attachment", "nav_menu", "spectra", "revision"]

# Паттерны для фильтрации служебных таксономий (по умолчанию)
DEFAULT_SKIP_TAXONOMY_PATTERNS = ["nav_menu", "link_category", "post_format"]

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

# ========================================
# Async Sync Configuration (NEW)
# ========================================

# API request timeout (seconds)
DEFAULT_API_TIMEOUT = 30

# Media upload timeout (seconds)
DEFAULT_MEDIA_TIMEOUT = 120

# Initial retry delay (seconds)
DEFAULT_RETRY_DELAY = 1.0

# Circuit breaker failure threshold
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 10

# Circuit breaker recovery timeout (seconds)
DEFAULT_CIRCUIT_BREAKER_RECOVERY = 60
