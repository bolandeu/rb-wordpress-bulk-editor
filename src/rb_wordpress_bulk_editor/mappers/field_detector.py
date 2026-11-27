"""
Автоопределение типов полей в Excel
"""

from typing import Dict, List, Optional

from ..constants import (
    SYSTEM_COLUMNS,
    ACF_PREFIX,
    is_dot_notation_column,
    is_taxonomy_column,
)


class FieldDetector:
    """Определяет типы полей из колонок Excel"""

    def __init__(
        self,
        columns: List[str],
        known_taxonomies: Optional[List[str]] = None,
    ):
        """
        Args:
            columns: Список колонок из Excel.
            known_taxonomies: Явный список таксономий проекта (из .env или WP discovery).
                Если задан — используется вместо STANDARD_TAXONOMIES + `-cat` fallback.
        """
        self.columns = columns
        self.known_taxonomies = known_taxonomies
        self._analysis_cache: Optional[Dict[str, List[str]]] = None

    def analyze(self) -> Dict[str, List[str]]:
        """
        Анализ колонок и группировка по типам.

        Returns:
            Dict с ключами:
            - system: служебные колонки
            - acf: ACF поля (без префикса acf_, только legacy-форма acf_xxx)
            - taxonomies: колонки таксономий
            - nested: dot-notation колонки (seo.title, meta._yoast..., acf.foo)
            - content: обычные плоские поля контента
        """
        if self._analysis_cache is not None:
            return self._analysis_cache

        result: Dict[str, List[str]] = {"system": [], "acf": [], "taxonomies": [], "nested": [], "content": []}

        system_lower = {c.lower() for c in SYSTEM_COLUMNS}

        for col in self.columns:
            col_lower = col.lower()

            # Служебные колонки
            if col in SYSTEM_COLUMNS or col_lower in system_lower:
                result["system"].append(col)
                continue

            # Таксономии (явный override имеет приоритет над dot-нотацией,
            # иначе `product_cat` из env-списка съест false-positive на колонку без точки)
            if is_taxonomy_column(col, self.known_taxonomies):
                result["taxonomies"].append(col)
                continue

            # Dot-notation — вложенные поля в payload
            if is_dot_notation_column(col):
                result["nested"].append(col)
                continue

            # Legacy ACF (acf_field_name)
            if col.startswith(ACF_PREFIX):
                field_name = col[len(ACF_PREFIX) :]
                result["acf"].append(field_name)
                continue

            # Все остальное — обычные поля контента
            result["content"].append(col)

        self._analysis_cache = result
        return result

    def get_acf_fields(self) -> List[str]:
        """Получить список ACF полей (legacy-форма, без префикса acf_)."""
        analysis = self.analyze()
        return analysis["acf"]

    def get_taxonomy_fields(self) -> List[str]:
        """Получить список колонок таксономий."""
        analysis = self.analyze()
        return analysis["taxonomies"]

    def get_content_fields(self) -> List[str]:
        """Получить список обычных плоских полей контента."""
        analysis = self.analyze()
        return analysis["content"]

    def get_nested_fields(self) -> List[str]:
        """Получить список dot-notation колонок (вложенные поля)."""
        analysis = self.analyze()
        return analysis["nested"]

    def get_api_fields(self) -> List[str]:
        """
        Получить список полей для передачи в WordPress API.
        Исключает служебные колонки.
        """
        analysis = self.analyze()
        return analysis["content"] + analysis["taxonomies"] + analysis["nested"]
