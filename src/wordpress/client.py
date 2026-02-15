"""
HTTP клиент для WordPress REST API
"""

import base64
from typing import Dict, Optional, Any
import httpx
from loguru import logger

from ..config import WordPressConfig
from ..exceptions import (
    AuthenticationError,
    NotFoundError,
    ServerError,
    NetworkError,
    WordPressError
)
from ..utils.retry import RetryConfig, CircuitBreaker, async_retry


class WordPressClient:
    """Асинхронный HTTP клиент для WordPress REST API"""

    def __init__(
        self,
        config: WordPressConfig,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        timeout: Optional[float] = None
    ):
        self.config = config
        self.base_url = config.url
        self._client: Optional[httpx.AsyncClient] = None

        # NEW: Retry и circuit breaker для async команд
        self.retry_config = retry_config or RetryConfig()
        self.circuit_breaker = circuit_breaker
        self.timeout = timeout or 30.0

    async def __aenter__(self):
        """Открытие клиента"""
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Закрытие клиента"""
        await self.close()

    async def open(self):
        """Создать HTTP клиент"""
        if self._client is None:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            # Добавляем Basic Auth только если есть credentials
            if self.config.username and self.config.password:
                auth_string = f"{self.config.username}:{self.config.password}"
                auth_bytes = auth_string.encode("utf-8")
                auth_b64 = base64.b64encode(auth_bytes).decode("ascii")
                headers["Authorization"] = f"Basic {auth_b64}"
                logger.debug(f"WordPress client создан с авторизацией для {self.base_url}")
            else:
                logger.debug(f"WordPress client создан БЕЗ авторизации для {self.base_url}")

            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                verify=self.config.verify_ssl,
                timeout=self.timeout,  # NEW: Configurable timeout
                follow_redirects=True
            )

    async def close(self):
        """Закрыть HTTP клиент"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("WordPress client закрыт")

    def _handle_error(self, response: httpx.Response):
        """Обработка ошибок HTTP"""
        if response.status_code == 401:
            raise AuthenticationError(
                f"Ошибка аутентификации. Проверьте credentials. "
                f"URL: {response.url}"
            )

        if response.status_code == 404:
            raise NotFoundError(
                f"Ресурс не найден: {response.url}"
            )

        if response.status_code >= 500:
            raise ServerError(
                f"Ошибка сервера WordPress ({response.status_code}): "
                f"{response.text[:200]}"
            )

        if response.status_code >= 400:
            try:
                error_data = response.json()
                message = error_data.get("message", response.text[:200])
            except Exception:
                message = response.text[:200]

            raise WordPressError(
                f"WordPress API error ({response.status_code}): {message}"
            )

    # ========================================
    # Internal methods with @async_retry (NEW)
    # ========================================

    @async_retry()
    async def _get_with_retry(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Внутренний GET метод с retry."""
        if self._client is None:
            await self.open()

        try:
            logger.debug(f"GET {endpoint} params={params}")
            response = await self._client.get(endpoint, params=params)

            logger.debug(f"GET {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                self._handle_error(response)

            try:
                data = response.json()
                return data
            except Exception as e:
                logger.error(
                    f"Failed to parse JSON response from {endpoint}. "
                    f"Status: {response.status_code}, "
                    f"Content-Type: {response.headers.get('content-type')}, "
                    f"Body: {response.text[:500]}"
                )
                raise WordPressError(
                    f"WordPress API returned invalid JSON. "
                    f"Status: {response.status_code}, "
                    f"Response: {response.text[:200]}"
                )

        except httpx.NetworkError as e:
            raise NetworkError(f"Сетевая ошибка: {e}")
        except httpx.TimeoutException as e:
            raise NetworkError(f"Timeout: {e}")

    @async_retry()
    async def _post_with_retry(
        self,
        endpoint: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Внутренний POST метод с retry."""
        if self._client is None:
            await self.open()

        try:
            if files:
                # Multipart для загрузки файлов
                logger.debug(f"POST {endpoint} (multipart upload)")
                logger.debug(f"Multipart data keys: {list(data.keys()) if data else 'None'}")
                logger.debug(f"Multipart files keys: {list(files.keys()) if files else 'None'}")
                for key, file_info in files.items():
                    logger.debug(f"File '{key}': name={file_info[0]}, MIME={file_info[2]}, size={len(file_info[1])} bytes")

                headers = self._client.headers.copy()
                headers.pop("Content-Type", None)

                response = await self._client.post(
                    endpoint,
                    data=data,
                    files=files,
                    headers=headers
                )
            else:
                # JSON
                logger.debug(f"POST {endpoint} with full data: {data}")
                response = await self._client.post(endpoint, json=data)

            logger.debug(f"POST {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                self._handle_error(response)

            try:
                result = response.json()
                return result
            except Exception as e:
                logger.error(
                    f"Failed to parse JSON response from {endpoint}. "
                    f"Status: {response.status_code}, "
                    f"Content-Type: {response.headers.get('content-type')}, "
                    f"Body: {response.text[:500]}"
                )
                raise WordPressError(
                    f"WordPress API returned invalid JSON. "
                    f"Status: {response.status_code}, "
                    f"Response: {response.text[:200]}"
                )

        except httpx.NetworkError as e:
            raise NetworkError(f"Сетевая ошибка: {e}")
        except httpx.TimeoutException as e:
            raise NetworkError(f"Timeout: {e}")

    @async_retry()
    async def _put_with_retry(
        self,
        endpoint: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Внутренний PUT метод с retry."""
        if self._client is None:
            await self.open()

        try:
            logger.debug(f"PUT {endpoint} with data keys: {list(data.keys())}")
            logger.debug(f"PUT {endpoint} with full data: {data}")
            response = await self._client.put(endpoint, json=data)

            logger.debug(f"PUT {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                self._handle_error(response)

            try:
                result = response.json()
                return result
            except Exception as e:
                logger.error(
                    f"Failed to parse JSON response from {endpoint}. "
                    f"Status: {response.status_code}, "
                    f"Content-Type: {response.headers.get('content-type')}, "
                    f"Body: {response.text[:500]}"
                )
                raise WordPressError(
                    f"WordPress API returned invalid JSON. "
                    f"Status: {response.status_code}, "
                    f"Response: {response.text[:200]}"
                )

        except httpx.NetworkError as e:
            raise NetworkError(f"Сетевая ошибка: {e}")
        except httpx.TimeoutException as e:
            raise NetworkError(f"Timeout: {e}")

    @async_retry()
    async def _delete_with_retry(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Внутренний DELETE метод с retry."""
        if self._client is None:
            await self.open()

        try:
            logger.debug(f"DELETE {endpoint} params={params}")
            response = await self._client.delete(endpoint, params=params)

            logger.debug(f"DELETE {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                self._handle_error(response)

            try:
                result = response.json()
                return result
            except Exception as e:
                logger.error(
                    f"Failed to parse JSON response from {endpoint}. "
                    f"Status: {response.status_code}, "
                    f"Content-Type: {response.headers.get('content-type')}, "
                    f"Body: {response.text[:500]}"
                )
                raise WordPressError(
                    f"WordPress API returned invalid JSON. "
                    f"Status: {response.status_code}, "
                    f"Response: {response.text[:200]}"
                )

        except httpx.NetworkError as e:
            raise NetworkError(f"Сетевая ошибка: {e}")
        except httpx.TimeoutException as e:
            raise NetworkError(f"Timeout: {e}")

    # ========================================
    # Public methods with circuit breaker
    # ========================================

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        GET запрос с retry и circuit breaker.

        Args:
            endpoint: Путь endpoint (например /wp-json/wp/v2/posts)
            params: Query параметры

        Returns:
            JSON ответ как словарь

        Raises:
            AuthenticationError, NotFoundError, ServerError, NetworkError, CircuitBreakerOpenError
        """
        if self.circuit_breaker:
            return await self.circuit_breaker.call(
                self._get_with_retry, endpoint, params
            )
        else:
            return await self._get_with_retry(endpoint, params)

    async def post(
        self,
        endpoint: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        POST запрос с retry и circuit breaker.

        Args:
            endpoint: Путь endpoint
            data: JSON данные для отправки
            files: Файлы для загрузки (multipart)

        Returns:
            JSON ответ как словарь

        Raises:
            AuthenticationError, NotFoundError, ServerError, NetworkError, CircuitBreakerOpenError
        """
        if self.circuit_breaker:
            return await self.circuit_breaker.call(
                self._post_with_retry, endpoint, data, files
            )
        else:
            return await self._post_with_retry(endpoint, data, files)

    async def put(
        self,
        endpoint: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        PUT запрос с retry и circuit breaker.

        Args:
            endpoint: Путь endpoint
            data: JSON данные для обновления

        Returns:
            JSON ответ как словарь

        Raises:
            AuthenticationError, NotFoundError, ServerError, NetworkError, CircuitBreakerOpenError
        """
        if self.circuit_breaker:
            return await self.circuit_breaker.call(
                self._put_with_retry, endpoint, data
            )
        else:
            return await self._put_with_retry(endpoint, data)

    async def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        DELETE запрос с retry и circuit breaker.

        Args:
            endpoint: Путь endpoint
            params: Query параметры

        Returns:
            JSON ответ как словарь

        Raises:
            AuthenticationError, NotFoundError, ServerError, NetworkError, CircuitBreakerOpenError
        """
        if self.circuit_breaker:
            return await self.circuit_breaker.call(
                self._delete_with_retry, endpoint, params
            )
        else:
            return await self._delete_with_retry(endpoint, params)

    async def test_connection(self) -> bool:
        """
        Проверка подключения к WordPress API.

        Returns:
            True если подключение успешно

        Raises:
            AuthenticationError, NetworkError
        """
        try:
            await self.get("/wp-json/wp/v2/")
            logger.info(f"✓ Подключение к WordPress API успешно: {self.base_url}")
            return True
        except Exception as e:
            logger.error(f"✗ Ошибка подключения к WordPress API: {e}")
            raise
