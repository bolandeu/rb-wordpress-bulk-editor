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

        # 1. Обычные поля контента
        for field in self.analysis["content"]:
            if field in row.index:
                value = row[field]

                # Пропускаем пустые значения
                if pd.notna(value) and value != "":
                    payload[field] = self._convert_value(value)

        # 2. ACF поля
        acf_data = FieldDetector.extract_acf_from_row(row)
        if acf_data:
            payload["acf"] = acf_data

        # 3. Таксономии (уже преобразованные в term IDs)
        if taxonomy_ids:
            for taxonomy, term_ids in taxonomy_ids.items():
                if term_ids:
                    payload[taxonomy] = term_ids

        # 4. Featured media (если есть)
        if "featured_media" in row.index and pd.notna(row["featured_media"]):
            value = row["featured_media"]
            if value != "":
                try:
                    payload["featured_media"] = int(value)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid featured_media value: {value}")

        logger.debug(f"Mapped row to payload: {list(payload.keys())}")
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
