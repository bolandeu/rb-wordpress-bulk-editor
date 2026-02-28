"""
Автоопределение типов полей в Excel
"""

from typing import Dict, List

from ..constants import (
    SYSTEM_COLUMNS,
    ACF_PREFIX,
    is_taxonomy_column,
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
            if is_taxonomy_column(col):
                result["taxonomies"].append(col)
                continue

            # Все остальное - обычные поля контента
            result["content"].append(col)

        self._analysis_cache = result
        return result

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

