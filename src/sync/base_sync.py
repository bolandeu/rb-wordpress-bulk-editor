"""
Базовый класс для sync операций.

Предоставляет общую логику для ContentSync, TaxonomySync и их async версий.
"""

from abc import ABC
from typing import Dict
from ..wordpress.client import WordPressClient
from ..config import AppConfig


class BaseSync(ABC):
    """
    Базовый класс для всех sync операций.

    Предоставляет:
    - Валидацию action
    - Инициализацию статистики
    - Общие вспомогательные методы
    """

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
        return action.lower() in {"add", "update", "delete", "skip"}

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
