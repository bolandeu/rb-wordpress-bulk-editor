"""
HTTP клиент для WordPress REST API.

Тонкая обёртка над `WordPressHttpProvider`: вся транспортная логика
(авторизация, verify_ssl, retry, circuit breaker, маппинг ошибок) живёт в
провайдере, здесь остаётся привычный для endpoint-ов и sync-движка интерфейс.
"""

from typing import Any, Dict, Optional

import httpx

from ..config import WordPressConfig
from ..providers.wordpress import WordPressHttpProvider
from ..utils.retry import CircuitBreaker, RetryConfig


class WordPressClient:
    """Асинхронный HTTP клиент для WordPress REST API"""

    def __init__(
        self,
        config: WordPressConfig,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        timeout: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.provider = WordPressHttpProvider(
            config=config,
            retry_config=retry_config,
            circuit_breaker=circuit_breaker,
            timeout=timeout,
            transport=transport,
        )

    # ── Доступ к состоянию провайдера ─────────────────────────────────

    @property
    def config(self) -> WordPressConfig:
        return self.provider.config

    @property
    def base_url(self) -> str:
        return self.provider.base_url

    @property
    def retry_config(self) -> RetryConfig:
        return self.provider.retry_config

    @property
    def circuit_breaker(self) -> Optional[CircuitBreaker]:
        return self.provider.circuit_breaker

    @property
    def timeout(self) -> float:
        return self.provider.timeout

    @property
    def _client(self) -> Optional[httpx.AsyncClient]:
        """Живой httpx-клиент. Тесты подменяют его после конструирования."""
        return self.provider._client

    @_client.setter
    def _client(self, value: Optional[httpx.AsyncClient]):
        self.provider._client = value

    # ── Жизненный цикл ────────────────────────────────────────────────

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def open(self):
        """Создать HTTP клиент"""
        await self.provider.open()

    async def close(self):
        """Закрыть HTTP клиент"""
        await self.provider.close()

    # ── Запросы ───────────────────────────────────────────────────────

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        suppress_non_retryable_log: bool = False,
    ) -> Dict[str, Any]:
        """GET запрос с retry и circuit breaker."""
        return await self.provider.get(
            endpoint, params, suppress_non_retryable_log=suppress_non_retryable_log
        )

    async def post(
        self,
        endpoint: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST запрос с retry и circuit breaker."""
        return await self.provider.post(endpoint, data, files)

    async def put(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """PUT запрос с retry и circuit breaker."""
        return await self.provider.put(endpoint, data)

    async def delete(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """DELETE запрос с retry и circuit breaker."""
        return await self.provider.delete(endpoint, params)

    async def test_connection(self, authenticated: bool = False) -> bool:
        """
        Проверка подключения к WordPress API.

        Returns:
            True если подключение успешно

        Raises:
            AuthenticationError, NetworkError
        """
        return await self.provider.test_connection(authenticated=authenticated)
