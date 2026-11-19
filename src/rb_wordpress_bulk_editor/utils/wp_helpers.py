"""
Вспомогательные функции для работы с WordPress API
"""

from ..constants import TAXONOMY_DELIMITER


def wp_rendered(field, default: str = "") -> str:
    """Извлечь rendered-значение из WordPress API поля (dict или строка)."""
    if isinstance(field, dict):
        return field.get("rendered", default)
    return str(field) if field else default


def convert_taxonomy_ids_to_names(
    term_ids: list,
    id_to_name: dict,
    taxonomy_name: str,
    delimiter: str = TAXONOMY_DELIMITER,
) -> str:
    """
    Преобразовать массив ID терминов в строку с названиями.

    Args:
        term_ids: Список ID
        id_to_name: Маппинг {id: name} для данной таксономии
        taxonomy_name: Имя таксономии (для логов)
        delimiter: Разделитель (по умолчанию '; ')

    Returns:
        Строка с названиями или пустая строка
    """
    from loguru import logger

    if not term_ids:
        return ""
    if not id_to_name:
        logger.warning(f"No cache for taxonomy '{taxonomy_name}', returning IDs")
        return delimiter.join(map(str, term_ids))

    names = []
    for term_id in term_ids:
        term_name = id_to_name.get(term_id)
        if term_name:
            names.append(term_name)
        else:
            logger.warning(f"Term ID {term_id} not found in {taxonomy_name} cache")
            names.append(str(term_id))

    return delimiter.join(names)
