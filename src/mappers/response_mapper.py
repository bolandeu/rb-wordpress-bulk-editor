"""
Преобразование ответов WordPress API в формат Excel
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import re

from ..constants import read_nested_value
from ..utils.wp_helpers import wp_rendered, convert_taxonomy_ids_to_names


LONG_CONTENT_THRESHOLD = 32000


class ResponseMapper:
    """
    Преобразует ответы WordPress REST API в формат для записи в Excel.
    """

    def __init__(
        self,
        taxonomy_cache: Optional[Dict[str, Dict[int, str]]] = None,
        nested_paths: Optional[List[str]] = None,
        content_output_dir: Optional[Path] = None,
        excel_cell_limit: bool = True,
    ):
        """
        Args:
            taxonomy_cache: Словарь {taxonomy_name: {term_id: term_name}}
            nested_paths: Dot-пути к вложенным полям, которые надо извлечь из ответа
                и записать в Excel-колонки с теми же именами (например, `seo.title`).
                Источник — обычно WP_SEO_FIELDS из .env проекта.
            content_output_dir: Папка для HTML файлов, если content не помещается
                в одну Excel ячейку.
            excel_cell_limit: False — контент любой длины остаётся в записи
                (выгрузка в JSON: ограничения ячейки Excel там нет).
        """
        self.taxonomy_cache = taxonomy_cache or {}
        self.nested_paths = nested_paths or []
        self.content_output_dir = content_output_dir
        self.excel_cell_limit = excel_cell_limit

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

        # Title, Content, Excerpt (могут быть объектами с 'rendered')
        row["title"] = wp_rendered(post.get("title", {}))
        row["content"] = wp_rendered(post.get("content", {}))
        row["content_path"] = ""
        self._move_long_content_to_file(row, post)
        row["excerpt"] = wp_rendered(post.get("excerpt", {}))
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
        row["categories"] = convert_taxonomy_ids_to_names(
            post.get("categories", []),
            self.taxonomy_cache.get("categories", {}),
            "categories",
        )
        row["tags"] = convert_taxonomy_ids_to_names(
            post.get("tags", []),
            self.taxonomy_cache.get("tags", {}),
            "tags",
        )

        # ACF поля
        acf = post.get("acf", {})
        if isinstance(acf, dict):
            for key, value in acf.items():
                row[f"acf_{key}"] = value

        # Dot-notation вложенные поля (например, WP_SEO_FIELDS=seo.title,seo.description)
        for path in self.nested_paths:
            value = read_nested_value(path, post)
            if value is not None and not isinstance(value, (dict, list)):
                row[path] = value

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
                        row[key] = convert_taxonomy_ids_to_names(
                            value, self.taxonomy_cache.get(key, {}), key,
                        )
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

    def _move_long_content_to_file(self, row: Dict[str, Any], post: Dict[str, Any]):
        """Сохранить слишком длинный HTML в файл, чтобы Excel не обрезал ячейку."""
        content = row.get("content")
        if not self.excel_cell_limit:
            return
        if not isinstance(content, str) or len(content) <= LONG_CONTENT_THRESHOLD:
            return

        if not self.content_output_dir:
            logger.warning(
                f"Content for post ID={post.get('id')} is too long for Excel "
                f"({len(content)} chars) and may be truncated"
            )
            return

        self.content_output_dir.mkdir(parents=True, exist_ok=True)
        post_id = post.get("id") or "no-id"
        slug = str(post.get("slug") or f"post-{post_id}")
        safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or f"post-{post_id}"
        filename = f"{post_id}-{safe_slug}.html"
        content_file = self.content_output_dir / filename

        content_file.write_text(content, encoding="utf-8")
        row["content"] = ""
        row["content_path"] = filename
        logger.info(
            f"Content for post ID={post_id} saved to {content_file} "
            f"({len(content)} chars)"
        )

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
        # (nested_paths не применяются к terms — они per-instance, решит вызывающий код если нужно)

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
