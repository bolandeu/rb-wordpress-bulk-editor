"""
Автоопределение типов полей в Excel
"""

from typing import Dict, List, Set
import pandas as pd

from ..constants import (
    SYSTEM_COLUMNS,
    ACF_PREFIX,
    TAXONOMY_SUFFIX,
    STANDARD_TAXONOMIES
)


class FieldDetector:
    """Определяет типы полей из колонок Excel"""

    def __init__(self, columns: List[str]):
        self.columns = columns
        self._analysis_cache = None

    def analyze(self) -> Dict[str, List[str]]:
        """
        Анализ колонок и группировка по типам.

        Returns:
            Dict с ключами:
            - system: служебные колонки
            - acf: ACF поля (без префикса)
            - taxonomies: колонки таксономий
            - content: обычные поля контента
        """
        if self._analysis_cache is not None:
            return self._analysis_cache

        result = {
            "system": [],
            "acf": [],
            "taxonomies": [],
            "content": []
        }

        for col in self.columns:
            col_lower = col.lower()

            # Служебные колонки
            if col in SYSTEM_COLUMNS or col_lower in {c.lower() for c in SYSTEM_COLUMNS}:
                result["system"].append(col)
                continue

            # ACF поля
            if col.startswith(ACF_PREFIX):
                # Убираем префикс acf_
                field_name = col[len(ACF_PREFIX):]
                result["acf"].append(field_name)
                continue

            # Таксономии
            if self._is_taxonomy_column(col):
                result["taxonomies"].append(col)
                continue

            # Все остальное - обычные поля контента
            result["content"].append(col)

        self._analysis_cache = result
        return result

    def _is_taxonomy_column(self, col: str) -> bool:
        """Проверяет является ли колонка таксономией"""
        col_lower = col.lower()

        # Стандартные таксономии
        if col_lower in {"categories", "tags", "category", "tag"}:
            return True

        # Кастомные с суффиксом -cat
        if col.endswith(TAXONOMY_SUFFIX):
            return True

        return False

    def get_acf_fields(self) -> List[str]:
        """Получить список ACF полей (без префикса acf_)"""
        analysis = self.analyze()
        return analysis["acf"]

    def get_taxonomy_fields(self) -> List[str]:
        """Получить список колонок таксономий"""
        analysis = self.analyze()
        return analysis["taxonomies"]

    def get_content_fields(self) -> List[str]:
        """Получить список обычных полей контента"""
        analysis = self.analyze()
        return analysis["content"]

    def get_api_fields(self) -> List[str]:
        """
        Получить список полей для передачи в WordPress API.
        Исключает служебные колонки.
        """
        analysis = self.analyze()
        return (
            analysis["content"] +
            analysis["taxonomies"]
        )

    @staticmethod
    def extract_acf_from_row(row: pd.Series) -> Dict[str, any]:
        """
        Извлечь ACF поля из строки Excel.

        Args:
            row: pandas Series (строка DataFrame)

        Returns:
            Dict с ACF полями
        """
        acf_data = {}

        for col in row.index:
            if col.startswith(ACF_PREFIX):
                field_name = col[len(ACF_PREFIX):]
                value = row[col]

                # Пропускаем пустые значения
                if pd.notna(value) and value != "":
                    # Конвертируем значение в правильный тип
                    acf_data[field_name] = FieldDetector._convert_acf_value(value)

        return acf_data

    @staticmethod
    def _convert_acf_value(value: any) -> any:
        """
        Конвертирует значение ACF поля в правильный тип.

        Строки, которые выглядят как числа, конвертируются в числа.
        Даты конвертируются в формат YYYYMMDD для ACF Date Picker.
        Это важно для WordPress ACF API.
        """
        from datetime import datetime

        if pd.isna(value):
            return None

        # Если уже число - вернуть как есть
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return value

        # Если строка - попробовать сконвертировать
        if isinstance(value, str):
            value = value.strip()

            # Проверяем формат даты YYYY-MM-DD HH:MM:SS или YYYY-MM-DD
            date_formats = [
                '%Y-%m-%d %H:%M:%S',  # 2020-04-24 00:00:00
                '%Y-%m-%d',            # 2020-04-24
                '%d.%m.%Y %H:%M:%S',   # 24.04.2020 00:00:00
                '%d.%m.%Y',            # 24.04.2020
                '%d/%m/%Y',            # 24/04/2020
            ]

            for date_format in date_formats:
                try:
                    dt = datetime.strptime(value, date_format)
                    # ACF Date Picker ожидает формат YYYYMMDD
                    return dt.strftime('%Y%m%d')
                except ValueError:
                    continue

            # Проверяем формат даты YYYYMMDD (8 цифр, начинается с 19 или 20)
            # Не конвертируем в число, оставляем строкой
            if len(value) == 8 and value.isdigit() and value[:2] in ('19', '20'):
                return value

            # Пробуем как целое число
            try:
                return int(value)
            except ValueError:
                pass

            # Пробуем как float
            try:
                float_val = float(value)
                if float_val.is_integer():
                    return int(float_val)
                return float_val
            except ValueError:
                pass

            # Булевы значения
            if value.lower() in ('true', 'yes', '1'):
                return True
            if value.lower() in ('false', 'no', '0'):
                return False

            # Возвращаем строку как есть
            return value

        return value

    @staticmethod
    def extract_taxonomies_from_row(
        row: pd.Series,
        taxonomy_columns: List[str]
    ) -> Dict[str, str]:
        """
        Извлечь значения таксономий из строки.

        Args:
            row: pandas Series
            taxonomy_columns: Список колонок таксономий

        Returns:
            Dict {taxonomy_name: value_string}
        """
        taxonomies = {}

        for col in taxonomy_columns:
            if col in row.index:
                value = row[col]
                if pd.notna(value) and value != "":
                    taxonomies[col] = str(value)

        return taxonomies

    @staticmethod
    def build_api_payload(
        row: pd.Series,
        content_fields: List[str],
        exclude_fields: Set[str] = None
    ) -> Dict[str, any]:
        """
        Построить payload для WordPress API из строки Excel.

        Args:
            row: pandas Series
            content_fields: Список полей контента для включения
            exclude_fields: Дополнительные поля для исключения

        Returns:
            Dict для отправки в API
        """
        exclude = exclude_fields or set()
        exclude.update(SYSTEM_COLUMNS)

        payload = {}

        for field in content_fields:
            if field in exclude:
                continue

            if field in row.index:
                value = row[field]

                # Пропускаем пустые значения и NaN
                if pd.notna(value) and value != "":
                    payload[field] = value

        return payload
