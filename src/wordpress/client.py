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


class WordPressClient:
    """Асинхронный HTTP клиент для WordPress REST API"""

    def __init__(self, config: WordPressConfig):
        self.config = config
        self.base_url = config.url
        self._client: Optional[httpx.AsyncClient] = None

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
                timeout=30.0,
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
            except:
                message = response.text[:200]

            raise WordPressError(
                f"WordPress API error ({response.status_code}): {message}"
            )

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        GET запрос.

        Args:
            endpoint: Путь endpoint (например /wp-json/wp/v2/posts)
            params: Query параметры

        Returns:
            JSON ответ как словарь

        Raises:
            AuthenticationError, NotFoundError, ServerError, NetworkError
        """
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

    async def post(
        self,
        endpoint: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        POST запрос.

        Args:
            endpoint: Путь endpoint
            data: JSON данные для отправки
            files: Файлы для загрузки (multipart)

        Returns:
            JSON ответ как словарь
        """
        if self._client is None:
            await self.open()

        try:
            logger.debug(f"POST {endpoint}")

            if files:
                # Multipart для загрузки файлов
                response = await self._client.post(
                    endpoint,
                    data=data,
                    files=files
                )
            else:
                # JSON
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

    async def put(
        self,
        endpoint: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """PUT запрос (обновление)."""
        if self._client is None:
            await self.open()

        try:
            logger.debug(f"PUT {endpoint} with data keys: {list(data.keys())}")
            response = await self._client.put(endpoint, json=data)

            logger.debug(f"PUT {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                self._handle_error(response)

            # Проверяем что ответ содержит JSON
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

    async def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """DELETE запрос."""
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
