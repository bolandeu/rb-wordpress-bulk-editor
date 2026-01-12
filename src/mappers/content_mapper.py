"""
Маппинг Excel строк в WordPress API payload
"""

from typing import Dict, Any, List, Set, Optional, Tuple
import pandas as pd
from loguru import logger

from .field_detector import FieldDetector
from ..constants import SYSTEM_COLUMNS


class ContentMapper:
    """
    Преобразует строки Excel в payload для WordPress REST API.
    """

    def __init__(self, columns: List[str]):
        """
        Args:
            columns: Список колонок из Excel
        """
        self.columns = columns
        self.detector = FieldDetector(columns)
        self.analysis = self.detector.analyze()

    def map_row(
        self,
        row: pd.Series,
        taxonomy_ids: Dict[str, List[int]] = None
    ) -> Dict[str, Any]:
        """
        Преобразовать строку Excel в WordPress API payload.

        Args:
            row: pandas Series (строка из DataFrame)
            taxonomy_ids: Словарь {taxonomy_name: [term_ids]} для таксономий

        Returns:
            Dict готовый для отправки в WordPress API
        """
        payload = {}
        
        # Логируем информацию о строке для отладки
        row_id = row.get("id", "N/A")
        row_title = row.get("title", "N/A")
        logger.debug(f"Mapping row id={row_id}, title='{row_title}'")
        
        # Полная структура строки для диагностики
        logger.debug(f"Row structure (all fields):")
        for field_name in row.index:
            value = row[field_name]
            logger.debug(f"  {field_name}: {repr(value)} (type={type(value).__name__}, isna={pd.isna(value)})")

        # 1. Обычные поля контента
        logger.debug(f"Processing content fields: {self.analysis['content']}")
        for field in self.analysis["content"]:
            if field in row.index:
                value = row[field]
                
                # Поля, которые нужно отправлять всегда (включая пустые значения для обнуления)
                send_empty_fields = {
                    'excerpt',  # excerpt нужно обнулять явно
                    'seo_title', 'seo_description', 'seo_meta_keywords',  # SEO поля
                    'seo_noindex', 'seo_nofollow', 'custom_permalink'  # Другие SEO поля
                }
                
                should_send_empty = field in send_empty_fields
                
                # Детальное логирование для SEO полей
                if field.startswith("seo_"):
                    logger.debug(
                        f"SEO field '{field}': raw_value={repr(value)}, "
                        f"type={type(value).__name__}, "
                        f"isna={pd.isna(value)}"
                    )

                # Для обычных полей пропускаем пустые значения
                # Для полей из send_empty_fields отправляем пустые значения
                if pd.notna(value) and value != "":
                    converted = self._convert_value(value)
                    payload[field] = converted
                    
                    # Детальное логирование для SEO полей
                    if field.startswith("seo_"):
                        logger.debug(
                            f"SEO field '{field}': added to payload as {repr(converted)}, "
                            f"type={type(converted).__name__}"
                        )
                elif should_send_empty:
                    # Отправляем пустое значение для обнуления на сайте
                    payload[field] = ""
                    logger.debug(f"Field '{field}': sending empty value to reset on server")

        # 2. ACF поля
        acf_data = FieldDetector.extract_acf_from_row(row)
        if acf_data:
            payload["acf"] = acf_data
            logger.debug(f"ACF fields: {list(acf_data.keys())}")

        # 3. Таксономии (уже преобразованные в term IDs)
        if taxonomy_ids:
            for taxonomy, term_ids in taxonomy_ids.items():
                if term_ids:
                    payload[taxonomy] = term_ids
            logger.debug(f"Taxonomies: {list(taxonomy_ids.keys())}")

        # 4. Featured media (если есть)
        if "featured_media" in row.index and pd.notna(row["featured_media"]):
            value = row["featured_media"]
            if value != "":
                try:
                    payload["featured_media"] = int(value)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid featured_media value: {value}")

        # Итоговый лог с payload
        logger.debug(f"Mapped row id={row_id} to payload keys: {list(payload.keys())}")
        
        # Детальный лог для SEO полей в финальном payload
        seo_fields_in_payload = {k: v for k, v in payload.items() if k.startswith("seo_")}
        if seo_fields_in_payload:
            logger.debug(f"SEO fields in final payload: {seo_fields_in_payload}")
        
        return payload

    def _convert_value(self, value: Any) -> Any:
        """
        Конвертировать значение в подходящий тип для API.

        Args:
            value: Значение из Excel

        Returns:
            Сконвертированное значение
        """
        # Если это pandas NaT или NaN
        if pd.isna(value):
            return None

        # Строка
        if isinstance(value, str):
            return value.strip()

        # Числа
        if isinstance(value, (int, float)):
            # Если это целое число в виде float
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return value

        # Булевы значения
        if isinstance(value, bool):
            return value

        # Остальное преобразуем в строку
        return str(value)

    def get_required_fields(self, action: str) -> Set[str]:
        """
        Получить список обязательных полей для действия.

        Args:
            action: Тип действия (add, update, delete)

        Returns:
            Множество обязательных полей
        """
        if action == "add":
            return {"title"}  # Для создания обязателен title

        if action in ("update", "delete"):
            return {"id"}  # Для update/delete обязателен ID

        return set()

    def validate_row(self, row: pd.Series, action: str) -> tuple[bool, Optional[str]]:
        """
        Валидация строки перед обработкой.

        Args:
            row: pandas Series
            action: Тип действия

        Returns:
            (is_valid, error_message)
        """
        required = self.get_required_fields(action)

        for field in required:
            if field not in row.index:
                return False, f"Отсутствует обязательное поле: {field}"

            value = row[field]
            if pd.isna(value) or value == "":
                return False, f"Поле '{field}' не может быть пустым"

        return True, None

    def extract_media_path(self, row: pd.Series) -> Optional[str]:
        """
        Извлечь путь к медиафайлу из колонки 'img_path'.

        Args:
            row: pandas Series

        Returns:
            Путь к медиа или None
        """
        if "img_path" not in row.index:
            return None

        value = row["img_path"]
        if pd.notna(value) and value != "":
            return str(value).strip()

        return None

    def should_upload_media(self, row: pd.Series) -> bool:
        """
        Проверить нужно ли загружать медиа для этой строки.

        Args:
            row: pandas Series

        Returns:
            True если нужно загрузить медиа
        """
        media_path = self.extract_media_path(row)
        return media_path is not None and media_path != ""
