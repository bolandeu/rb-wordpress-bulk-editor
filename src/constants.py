"""
Константы для WordPress Bulk Editor
"""

import re
from typing import Any, Dict, Iterable, Optional

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

# Короткие и альтернативные написания action → каноническое значение.
# Регистр не важен. Всё, чего здесь нет и что не входит в VALID_ACTIONS,
# остаётся как есть и дальше считается недопустимым (запись пропускается).
ACTION_ALIASES = {
    "u": "update", "e": "update", "edit": "update",
    "a": "add",
    "s": "skip", "0": "skip",
    "d": "delete",
}


def normalize_action(value: Any) -> str:
    """Привести значение action к каноническому виду.

    Пустое значение (None, NaN, "") → "". Число 0 из Excel (0 / 0.0) → "skip".
    Неизвестное значение возвращается в нижнем регистре как есть.
    """
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return "skip"
    text = str(value).strip().lower()
    return ACTION_ALIASES.get(text, text)

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

# Разделитель уровней иерархии внутри одного термина: "Родитель>Ребёнок"
TAXONOMY_HIERARCHY_DELIMITER = ">"

# ACF префикс
ACF_PREFIX = "acf_"

# Имя колонки-таксономии: части через `_`/`-`, одна из частей — корень.
# Сравнение по целой части, а не по подстроке: `catalog_url`, `category_id`
# и `terms_of_use` — обычные поля, а не таксономии.
TAXONOMY_NAME_ROOTS = frozenset({"cat", "tag", "tax", "term"})
_TAXONOMY_NAME_SEPARATORS = re.compile(r"[-_]")

# Поля, которые всегда отправляются как bool.
# Если колонка присутствует в Excel — значение парсится (пустое = True),
# отсутствие колонки = не отправляем (WP сохраняет текущее значение).
BOOL_FIELDS_DEFAULT_TRUE = frozenset({
    "seo.robots.index",
    "seo.robots.follow",
    "seo.sitemap.include",
})

# WordPress REST API базовый path
WP_API_BASE = "/wp-json/wp/v2"

# Timeout для HTTP запросов (секунды)
DEFAULT_TIMEOUT = 30


def is_taxonomy_column(col: str, known_taxonomies: Optional[Iterable[str]] = None) -> bool:
    """Проверяет является ли колонка (или имя листа) таксономией.

    Args:
        col: Имя колонки/листа.
        known_taxonomies: Явный список slug-ов таксономий (из .env или WP discovery).
            Если задан — имя колонки сравнивается по этому списку; fallback на
            STANDARD_TAXONOMIES и суффиксы TAXONOMY_SUFFIXES сохраняется.

    Соглашение об именах: имя разбито `_`/`-` на части, и одна из частей —
    `cat`, `tag`, `tax` или `term` (регистр не важен), в любом месте:
    `rb_services_cat`, `genre-cat`, `cat_services`, `region_term`.

    Эвристика по имени не применяется к dot-нотации (`acf.product_cat`,
    `seo.meta_tag`) и к legacy ACF-префиксу (`acf_service_cat`): это явно
    размеченные поля payload, а не таксономии. Явные списки
    (`known_taxonomies`, STANDARD_TAXONOMIES) — осознанный override и работают
    для любых имён.
    """
    col_lower = col.lower()
    if known_taxonomies:
        known_lower = {t.lower() for t in known_taxonomies}
        if col_lower in known_lower:
            return True
    if col_lower in STANDARD_TAXONOMIES:
        return True
    if is_dot_notation_column(col) or col_lower.startswith(ACF_PREFIX):
        return False
    parts = _TAXONOMY_NAME_SEPARATORS.split(col_lower)
    return len(parts) > 1 and all(parts) and any(p in TAXONOMY_NAME_ROOTS for p in parts)


def is_dot_notation_column(col: str) -> bool:
    """Признак новой dot-нотации: `seo.title`, `meta._yoast_wpseo_title`, `acf.group.subfield`.

    Пустые сегменты (`seo..title`) — невалидны, такие колонки остаются плоскими.
    """
    if "." not in col:
        return False
    parts = col.split(".")
    return all(p for p in parts)


def build_nested_payload(dot_path: str, value: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Положить значение в payload по dot-пути, создавая вложенные dict-ы.

    Пример:
        build_nested_payload("seo.og.title", "X", {}) -> {"seo": {"og": {"title": "X"}}}

    Если по пути уже есть не-dict значение — оно перезаписывается (последний wins).
    Мутирует и возвращает `payload`.
    """
    if not dot_path:
        return payload
    parts = dot_path.split(".")
    cursor = payload
    for key in parts[:-1]:
        existing = cursor.get(key)
        if not isinstance(existing, dict):
            existing = {}
            cursor[key] = existing
        cursor = existing
    cursor[parts[-1]] = value
    return payload


def read_nested_value(dot_path: str, source: Dict[str, Any]) -> Any:
    """Прочитать значение из вложенного dict по dot-пути. None если путь отсутствует."""
    if not dot_path:
        return None
    cursor: Any = source
    for key in dot_path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor

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

# API request timeout (seconds) — алиас DEFAULT_TIMEOUT
DEFAULT_API_TIMEOUT = DEFAULT_TIMEOUT

# Media upload timeout (seconds)
DEFAULT_MEDIA_TIMEOUT = 120

# Initial retry delay (seconds)
DEFAULT_RETRY_DELAY = 1.0

# Circuit breaker failure threshold
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 10

# Circuit breaker recovery timeout (seconds)
DEFAULT_CIRCUIT_BREAKER_RECOVERY = 60


# ========================================
# Shared helpers (перенесены в utils/)
# ========================================
# wp_rendered, convert_taxonomy_ids_to_names → src/utils/wp_helpers.py
# resolve_media_file → src/utils/path_utils.py
