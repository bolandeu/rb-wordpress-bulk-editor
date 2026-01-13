"""
Преобразование ответов WordPress API в формат Excel
"""

from typing import Dict, Any, Optional
from loguru import logger
import re

class ResponseMapper:
    """
    Преобразует ответы WordPress REST API в формат для записи в Excel.
    """

    def __init__(self, taxonomy_cache: Optional[Dict[str, Dict[int, str]]] = None):
        """
        Args:
            taxonomy_cache: Словарь {taxonomy_name: {term_id: term_name}}
        """
        self.taxonomy_cache = taxonomy_cache or {}

    def map_post_to_excel(self, post: Dict[str, Any]) -> Dict[str, Any]:
        """
        Преобразовать пост из WordPress API в формат Excel row.

        Args:
            post: Данные поста от WordPress API

        Returns:
            Словарь с данными для Excel строки
        """
        row = {}

        # Action по умолчанию при fetch
        row["action"] = "skip"

        # Базовые поля
        row["id"] = post.get("id", "")
        row["date"] = post.get("date", "")
        row["modified"] = post.get("modified", "")
        row["slug"] = post.get("slug", "")
        row["status"] = post.get("status", "")
        row["type"] = post.get("type", "")
        row["link"] = post.get("link", "")

        # Title (может быть объектом с 'rendered')
        title = post.get("title", {})
        if isinstance(title, dict):
            row["title"] = title.get("rendered", "")
        else:
            row["title"] = str(title)

        # Content
        content = post.get("content", {})
        if isinstance(content, dict):
            row["content"] = content.get("rendered", "")
        else:
            row["content"] = str(content)

        # Excerpt
        excerpt = post.get("excerpt", {})
        if isinstance(excerpt, dict):
            row["excerpt"] = excerpt.get("rendered", "")
        else:
            row["excerpt"] = str(excerpt)
        if row["excerpt"]:
            row["excerpt"] = re.sub(r"<[^>]+>", "", row["excerpt"]).strip()

        # Featured media
        row["featured_media"] = post.get("featured_media", "")

        # Parent (для pages)
        row["parent"] = post.get("parent", "")

        # Image URL (кастомное поле)
        row["image_url"] = post.get("image_url", "")

        # Таксономии (categories, tags)
        # API возвращает массивы ID, преобразуем в названия
        row["categories"] = self._convert_taxonomy_ids_to_names(
            post.get("categories", []), "categories"
        )
        row["tags"] = self._convert_taxonomy_ids_to_names(
            post.get("tags", []), "tags"
        )

        # ACF поля
        acf = post.get("acf", {})
        if isinstance(acf, dict):
            for key, value in acf.items():
                row[f"acf_{key}"] = value

        # SEO и другие кастомные поля
        # Добавляем все остальные поля которые не обработали
        skip_fields = {
            "id", "date", "modified", "slug", "status", "type", "link",
            "title", "content", "excerpt", "featured_media", "parent",
            "image_url", "categories", "tags", "acf",
            # Служебные поля API
            "_links", "guid", "author", "comment_status", "ping_status",
            "sticky", "template", "format", "meta", "menu_order"
        }

        for key, value in post.items():
            if key not in skip_fields and key not in row:
                # Проверяем, является ли это таксономией (список ID)
                if isinstance(value, list) and value and all(isinstance(x, int) for x in value):
                    # Это похоже на таксономию - список ID
                    # Проверяем есть ли в кэше
                    if key in self.taxonomy_cache:
                        row[key] = self._convert_taxonomy_ids_to_names(value, key)
                        logger.debug(f"Converted custom taxonomy '{key}' IDs to names")
                    else:
                        # Нет в кэше, записываем как строку
                        row[key] = "; ".join(map(str, value))
                        logger.debug(f"No cache for taxonomy '{key}', output as IDs")
                # Простые типы записываем как есть
                elif isinstance(value, (str, int, float, bool)):
                    row[key] = value
                # Сложные типы (dict, other lists) пропускаем
                elif value and not isinstance(value, (dict, list)):
                    row[key] = str(value)

        logger.debug(f"Mapped post ID={post.get('id')} to Excel row")
        return row

    @staticmethod
    def map_term_to_excel(term: Dict[str, Any]) -> Dict[str, Any]:
        """
        Преобразовать термин таксономии в формат Excel row.

        Args:
            term: Данные термина от WordPress API

        Returns:
            Словарь с данными для Excel строки
        """
        row = {}

        # Action по умолчанию при fetch
        row["action"] = "skip"

        # Базовые поля
        row["id"] = term.get("id", "")
        row["name"] = term.get("name", "")
        row["slug"] = term.get("slug", "")
        row["description"] = term.get("description", "")
        row["link"] = term.get("link", "")

        # ACF поля для таксономий
        acf = term.get("acf", {})
        if isinstance(acf, dict):
            for key, value in acf.items():
                row[f"acf_{key}"] = value

        # SEO и другие кастомные поля
        # Пропускаем уже обработанные и системные поля API
        skip_fields = {
            "id", "name", "slug", "description", "link", "acf",
            "_links", "taxonomy", "count", "parent"
        }

        for key, value in term.items():
            if key not in skip_fields and key not in row:
                if isinstance(value, (str, int, float, bool)):
                    row[key] = value
                elif value and not isinstance(value, (dict, list)):
                    row[key] = str(value)

        logger.debug(f"Mapped term ID={term.get('id')} to Excel row")
        return row

    def _convert_taxonomy_ids_to_names(
        self, term_ids: list, taxonomy: str
    ) -> str:
        """
        Преобразовать массив ID терминов в строку с названиями.

        Args:
            term_ids: Список ID терминов
            taxonomy: Название таксономии (categories, tags, etc.)

        Returns:
            Строка с названиями через "; " или пустая строка
        """
        if not term_ids:
            return ""

        # Если нет кэша для этой таксономии, возвращаем ID
        if taxonomy not in self.taxonomy_cache:
            logger.warning(
                f"No cache for taxonomy '{taxonomy}', returning IDs"
            )
            return ", ".join(map(str, term_ids))

        # Преобразуем ID в названия
        names = []
        for term_id in term_ids:
            term_name = self.taxonomy_cache[taxonomy].get(term_id)
            if term_name:
                names.append(term_name)
            else:
                logger.warning(
                    f"Term ID {term_id} not found in {taxonomy} cache"
                )
                names.append(str(term_id))  # Fallback на ID

        return "; ".join(names)
