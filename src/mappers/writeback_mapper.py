"""
Построение данных обратной записи из ответа WordPress API.

Логика общая для всех источников: обновляются только те поля, которые уже
есть в записи (колонки листа Excel / ключи JSON-записи).
"""

import re
from typing import Any, Dict, Iterable, List, Optional

from ..utils.wp_helpers import convert_taxonomy_ids_to_names


def normalize_api_value(value: Any) -> Any:
    """
    Нормализовать значение из WordPress API для записи в Excel.

    WordPress API возвращает некоторые поля как объекты:
    - title: {'raw': 'Title', 'rendered': 'Title'}
    - content: {'raw': '...', 'rendered': '...', 'protected': False}
    - excerpt: {'raw': '...', 'rendered': '...'}
    - name (taxonomies): иногда тоже объект

    Args:
        value: Значение из API

    Returns:
        Нормализованное значение (строка или исходное значение)
    """
    if isinstance(value, dict):
        # Если есть 'raw' - берём его (это исходное значение без HTML обёртки)
        if "raw" in value:
            return value["raw"]
        # Если есть 'rendered' но нет 'raw' - берём rendered
        if "rendered" in value:
            return value["rendered"]
    return value


def build_update_data_from_api(
    api_response: Dict[str, Any],
    action: str,
    existing_fields: Iterable[str],
    taxonomy_cache: Optional[Any] = None,
    columns_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Построить словарь данных для обновления записи из ответа WordPress API.

    Args:
        api_response: Ответ от WordPress API
        action: Тип операции (add, update, delete)
        existing_fields: Поля, которые есть в записи (колонки листа / ключи JSON)
        taxonomy_cache: TaxonomyCache для конвертации ID таксономий в имена
        columns_filter: Если задан — минимальный writeback (action, modified, id)

    Returns:
        Словарь {field_name: value} для обновления
    """
    update_data: Dict[str, Any] = {}
    existing_columns = list(existing_fields)

    # --- Режим columns_filter: минимальный writeback ---
    if columns_filter:
        if action == "add" and "id" in api_response and "id" in existing_columns:
            update_data["id"] = api_response["id"]
        if "modified" in api_response and "modified" in existing_columns:
            update_data["modified"] = api_response["modified"]
        if "action" in existing_columns:
            update_data["action"] = "skip"
        return update_data

    # --- Стандартный полный writeback ---

    # ID (для add операций)
    if action == "add" and "id" in api_response and "id" in existing_columns:
        update_data["id"] = api_response["id"]

    # Даты
    if "date" in api_response and "date" in existing_columns:
        update_data["date"] = api_response["date"]

    if "modified" in api_response and "modified" in existing_columns:
        update_data["modified"] = api_response["modified"]

    # Ссылка
    if "link" in api_response and "link" in existing_columns:
        update_data["link"] = api_response["link"]

    # Slug
    if "slug" in api_response and "slug" in existing_columns:
        update_data["slug"] = api_response["slug"]

    # Featured media
    if "featured_media" in api_response and api_response["featured_media"] and "featured_media" in existing_columns:
        update_data["featured_media"] = api_response["featured_media"]

    # Image URL (кастомное поле из API)
    if "image_url" in api_response and "image_url" in existing_columns:
        update_data["image_url"] = api_response["image_url"]

    # Поля с rendered/raw структурой - извлекаем raw значение
    # title, content (posts), name, description (taxonomies)
    for field in ("title", "content", "name", "description"):
        if field in api_response and field in existing_columns:
            update_data[field] = normalize_api_value(api_response[field])

    # Excerpt - извлекаем raw значение и очищаем от HTML тегов
    if "excerpt" in api_response and "excerpt" in existing_columns:
        excerpt_value = normalize_api_value(api_response["excerpt"])
        if excerpt_value:
            # Убираем HTML теги и обрезаем пробелы
            excerpt_value = re.sub(r"<[^>]+>", "", str(excerpt_value)).strip()
        update_data["excerpt"] = excerpt_value

    # Таксономии - конвертируем ID обратно в имена через "; "
    taxonomy_fields = {"categories", "tags"}
    # Добавляем кастомные таксономии из кэша
    if taxonomy_cache:
        taxonomy_fields.update(taxonomy_cache.cache.keys())

    for tax_field in taxonomy_fields:
        if tax_field in api_response and tax_field in existing_columns:
            tax_ids = api_response[tax_field]
            if isinstance(tax_ids, list) and tax_ids:
                # Конвертируем ID в имена
                id_to_name = {}
                if taxonomy_cache:
                    mapping = taxonomy_cache.get_id_to_name_mapping()
                    id_to_name = mapping.get(tax_field, {})
                update_data[tax_field] = convert_taxonomy_ids_to_names(
                    tax_ids,
                    id_to_name,
                    tax_field,
                )

    # ACF поля из ответа API (обновляем только те, что есть в записи)
    acf = api_response.get("acf", {})
    if acf and isinstance(acf, dict):
        for key, value in acf.items():
            acf_col = f"acf_{key}"
            if acf_col in existing_columns:
                update_data[acf_col] = normalize_api_value(value)

    # Другие кастомные поля (обновляем только те, что есть в записи)
    skip_fields = {
        "id",
        "date",
        "modified",
        "link",
        "slug",
        "featured_media",
        "image_url",
        "acf",
        "title",
        "content",
        "excerpt",
        "name",
        "description",
    }
    # Добавляем таксономии в skip_fields
    skip_fields.update(taxonomy_fields)

    for key, value in api_response.items():
        if key not in skip_fields and key not in update_data and key in existing_columns:
            update_data[key] = normalize_api_value(value)

    # После успешной операции меняем action на skip
    if "action" in existing_columns:
        update_data["action"] = "skip"

    # img_path очищаем: повтор с тем же путём загрузил бы картинку дублем.
    # content_path остаётся — это ссылка на исходный файл статьи.
    if "img_path" in existing_columns:
        update_data["img_path"] = ""

    return update_data
