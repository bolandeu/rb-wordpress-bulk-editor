"""
Создание Excel файлов с правильной структурой для автоматической выгрузки
"""

from pathlib import Path
from typing import List, Dict
from loguru import logger
from openpyxl import Workbook


class ExcelCreator:
    """
    Создание Excel файлов с правильной структурой колонок.
    """

    # Базовые колонки для типов записей
    POST_BASE_COLUMNS = [
        "action",
        "id",
        "date",
        "modified",
        "title",
        "type",
        "slug",
        "link",
        "parent",
        "status",
        "featured_media",
        "image_url",
        "excerpt",
        "content_path",
        "content",
    ]

    # SEO колонки (добавляются всегда)
    POST_SEO_COLUMNS = [
        "seo_title",
        "seo_description",
        "seo_meta_keywords",
        "seo_noindex",
        "seo_nofollow",
        "custom_permalink",
    ]

    # Стандартные таксономии
    POST_STANDARD_TAXONOMIES = ["categories", "tags"]

    # Базовые колонки для таксономий
    TAXONOMY_BASE_COLUMNS = ["action", "id", "name", "slug", "link", "description"]

    # SEO колонки для таксономий
    TAXONOMY_SEO_COLUMNS = [
        "seo_focus_keyword",
        "seo_meta_keywords",
        "seo_title",
        "seo_description",
        "seo_noindex",
        "seo_nofollow",
        "custom_permalink",
    ]

    def create_file_with_fields(self, file_path: Path, sheets_fields: Dict[str, List[str]]):
        """
        Создать Excel файл со всеми листами на основе реальных полей.

        Args:
            file_path: Путь к создаваемому файлу
            sheets_fields: Поля для каждого листа
                {
                    "posts": ["action", "id", "title", "content", ...],
                    "category": ["action", "id", "name", "slug", ...]
                }
        """
        logger.info(f"Создание Excel файла: {file_path}")
        logger.info(f"Количество листов: {len(sheets_fields)}")

        # Создаём workbook
        wb = Workbook()

        # Удаляем дефолтный лист
        if "Sheet" in wb.sheetnames:
            wb.remove(wb["Sheet"])

        # Создаём листы для каждого типа
        for sheet_name, columns in sheets_fields.items():
            # Создаём лист с заголовками
            self._create_empty_sheet(wb, sheet_name, columns)
            logger.debug(f"Создан лист '{sheet_name}' с {len(columns)} колонками")

        # Сохраняем файл
        wb.save(file_path)
        logger.info(f"Excel файл успешно создан: {file_path}")

    def create_file_with_metadata(self, file_path: Path, sheets_metadata: Dict[str, Dict]):
        """
        Создать Excel файл со всеми листами (старый метод для совместимости).

        Args:
            file_path: Путь к создаваемому файлу
            sheets_metadata: Метаданные для каждого листа
                {
                    "posts": {
                        "type": "content",
                        "acf_fields": ["field1", "field2"],
                        "custom_taxonomies": ["product_cat"]
                    },
                    "category": {
                        "type": "taxonomy",
                        "acf_fields": ["term_field1"]
                    }
                }
        """
        logger.info(f"Создание Excel файла: {file_path}")
        logger.info(f"Количество листов: {len(sheets_metadata)}")

        # Создаём workbook
        wb = Workbook()

        # Удаляем дефолтный лист
        if "Sheet" in wb.sheetnames:
            wb.remove(wb["Sheet"])

        # Создаём листы для каждого типа
        for sheet_name, metadata in sheets_metadata.items():
            sheet_type = metadata.get("type")

            if sheet_type == "content":
                # Лист для типа записи
                acf_fields = metadata.get("acf_fields", [])
                custom_taxonomies = metadata.get("custom_taxonomies", [])
                columns = self._build_post_columns(acf_fields, custom_taxonomies)

            elif sheet_type == "taxonomy":
                # Лист для таксономии
                acf_fields = metadata.get("acf_fields", [])
                columns = self._build_taxonomy_columns(acf_fields)

            else:
                logger.warning(f"Неизвестный тип листа '{sheet_type}' для '{sheet_name}', пропускаем")
                continue

            # Создаём лист с заголовками
            self._create_empty_sheet(wb, sheet_name, columns)
            logger.debug(f"Создан лист '{sheet_name}' с {len(columns)} колонками")

        # Сохраняем файл
        wb.save(file_path)
        logger.info(f"Excel файл успешно создан: {file_path}")

    def _build_post_columns(self, acf_fields: List[str], custom_taxonomies: List[str]) -> List[str]:
        """
        Построить список колонок для типа записи.

        Порядок:
        1. BASE_COLUMNS
        2. SEO колонки: WP_SEO_FIELDS из .env, иначе POST_SEO_COLUMNS (legacy)
        3. STANDARD_TAXONOMIES
        4. custom_taxonomies
        5. acf_* fields

        Args:
            acf_fields: Список ACF полей
            custom_taxonomies: Список кастомных таксономий

        Returns:
            Список названий колонок
        """
        columns = (
            self.POST_BASE_COLUMNS.copy()
            + self._seo_columns(self.POST_SEO_COLUMNS)
            + self.POST_STANDARD_TAXONOMIES.copy()
            + custom_taxonomies
            + [f"acf_{field}" for field in acf_fields]
        )

        logger.debug(f"Построен список колонок для записей: {len(columns)} колонок")
        return columns

    @staticmethod
    def _seo_columns(legacy_default: List[str]) -> List[str]:
        """SEO-колонки: WP_SEO_FIELDS из .env, fallback на переданный legacy-список."""
        from ..config import Settings

        return Settings().get_seo_fields() or legacy_default.copy()

    def _build_taxonomy_columns(self, acf_fields: List[str]) -> List[str]:
        """
        Построить список колонок для таксономии.

        Порядок:
        1. BASE_COLUMNS
        2. SEO колонки: WP_SEO_FIELDS из .env, иначе TAXONOMY_SEO_COLUMNS (legacy)
        3. acf_* fields

        Args:
            acf_fields: Список ACF полей

        Returns:
            Список названий колонок
        """
        columns = (
            self.TAXONOMY_BASE_COLUMNS.copy()
            + self._seo_columns(self.TAXONOMY_SEO_COLUMNS)
            + [f"acf_{field}" for field in acf_fields]
        )

        logger.debug(f"Построен список колонок для таксономии: {len(columns)} колонок")
        return columns

    def _create_empty_sheet(self, workbook: Workbook, sheet_name: str, columns: List[str]):
        """
        Создать пустой лист с заголовками.

        Args:
            workbook: Workbook openpyxl
            sheet_name: Название листа
            columns: Список колонок (заголовки)
        """
        # Создаём новый лист
        ws = workbook.create_sheet(title=sheet_name)

        # Записываем заголовки в первую строку
        for col_idx, col_name in enumerate(columns, start=1):
            ws.cell(row=1, column=col_idx, value=col_name)

        logger.debug(f"Создан лист '{sheet_name}' с заголовками")
