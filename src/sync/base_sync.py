"""
Базовый класс для sync операций.

Предоставляет общую логику для ContentSync, TaxonomySync и их async версий.
"""

from abc import ABC
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Sequence
from ..constants import VALID_ACTIONS, normalize_action
from ..records.base import Record, is_blank
from ..wordpress.client import WordPressClient
from ..config import AppConfig


class BaseSync(ABC):
    """
    Базовый класс для всех sync операций.

    Предоставляет:
    - Валидацию action
    - Инициализацию статистики
    - Группировку записей по action
    - Общие вспомогательные методы
    """

    # Порядок обработки: сначала удаляем, потом обновляем, потом создаём
    ACTION_ORDER = ("delete", "update", "add")

    def __init__(self, wp_client: WordPressClient, config: AppConfig):
        """
        Инициализация базового sync класса.

        Args:
            wp_client: WordPress API клиент
            config: Конфигурация приложения
        """
        self.wp_client = wp_client
        self.config = config

    def _validate_action(self, action: str) -> bool:
        """
        Валидация action значения.

        Args:
            action: Значение для валидации

        Returns:
            True если action валидный, False иначе
        """
        return normalize_action(action) in VALID_ACTIONS

    def _init_stats(self) -> Dict[str, int]:
        """
        Инициализация статистики для sync операции.

        Returns:
            Словарь с нулевыми счетчиками:
            - added: количество добавленных записей
            - updated: количество обновленных записей
            - deleted: количество удаленных записей
            - errors: количество ошибок
            - skipped: количество пропущенных записей
            - total: общее количество обработанных строк
        """
        return {
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "errors": 0,
            "skipped": 0,
            "total": 0
        }

    @staticmethod
    async def _tracked(task: Awaitable[Any], record: Record, action: str, progress: Optional[Any]) -> Any:
        """Дождаться задачи async-обработки записи и сразу отметить прогресс.

        Отметка — в момент завершения записи, а не после gather всей пачки:
        иначе в логе все строки [n/total] появились бы разом в конце.
        """
        result = await task
        if progress:
            if getattr(result, "success", False):
                progress.record_done(record, action, result.api_response)
            else:
                progress.record_done(record, action, error=True)
        return result

    @staticmethod
    def _record_action(record: Record) -> str:
        """Нормализованное значение action записи ("" если поле пустое/отсутствует)."""
        value = record.data.get("action")
        if is_blank(value):
            return ""
        return normalize_action(value)

    @classmethod
    def _group_by_action(cls, records: Sequence[Record]) -> Dict[str, List[Record]]:
        """Разложить записи по action в порядке ACTION_ORDER."""
        grouped: Dict[str, List[Record]] = {action: [] for action in cls.ACTION_ORDER}
        for record in records:
            action = cls._record_action(record)
            if action in grouped:
                grouped[action].append(record)
        return grouped

    @classmethod
    def _count_skipped(cls, records: Iterable[Record]) -> int:
        """Сколько записей не попало ни в один валидный action."""
        return sum(1 for r in records if cls._record_action(r) not in cls.ACTION_ORDER)
