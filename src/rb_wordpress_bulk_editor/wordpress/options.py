"""
WordPress Site Options Endpoint
"""

from typing import Dict, Any
from loguru import logger

from .client import WordPressClient


class OptionsEndpoint:
    """
    Endpoint для обновления настроек сайта через кастомный API.

    Использует endpoint: /wp-json/siteoptions/v1/options
    """

    def __init__(self, client: WordPressClient):
        self.client = client
        self.endpoint = "/wp-json/siteoptions/v1/options"

    async def get(self) -> Dict[str, Any]:
        """
        Получить настройки сайта из WordPress.

        Returns:
            Словарь с настройками {option_name: value}

        Example:
            >>> options = await endpoint.get()
            >>> print(options["site_title"])
        """
        logger.info("Получение настроек сайта из WordPress")

        result = await self.client.get(self.endpoint)

        # Значения опций (smtp_password, токены) в лог не пишем — только имена ключей.
        logger.info(f"Получено настроек: {len(result)}")
        logger.debug(f"Ключи опций: {list(result.keys())}")
        return result

    async def update(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обновить настройки сайта.

        Args:
            options: Словарь с настройками {option_name: value}

        Returns:
            Ответ от API

        Example:
            >>> options = {
            ...     "site_title": "My Site",
            ...     "posts_per_page": 10
            ... }
            >>> result = await endpoint.update(options)
        """
        logger.info(f"Обновление настроек сайта: {len(options)} опций")
        logger.debug(f"Ключи опций: {list(options.keys())}")

        result = await self.client.post(self.endpoint, options)

        logger.info("Настройки обновлены успешно")
        return result
