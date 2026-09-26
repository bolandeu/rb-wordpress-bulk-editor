"""
Провайдер WordPress REST API.

Здесь живёт вся специфика WordPress поверх общего `HttpProvider`:
Basic Auth из credentials, `verify_ssl` из конфига, multipart-загрузка
и бинарная загрузка медиа (с терпимым разбором ответа кривого PHP-хостинга).
"""

import base64
from typing import Any, Callable, Dict, Optional

import httpx
from loguru import logger

from ..config import WordPressConfig
from ..utils.retry import CircuitBreaker, RetryConfig
from .http import HttpProvider


class WordPressHttpProvider(HttpProvider):
    """Асинхронный провайдер WordPress REST API."""

    name = "wordpress"

    def __init__(
        self,
        config: WordPressConfig,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        timeout: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.config = config
        super().__init__(
            base_url=config.url,
            # Единственная точка сборки verify_ssl — WordPressConfig.from_credentials()
            verify=config.verify_ssl,
            timeout=timeout,
            retry_config=retry_config,
            circuit_breaker=circuit_breaker,
            transport=transport,
        )

    def auth_headers(self) -> Dict[str, str]:
        """Basic Auth из username/password. Без credentials — анонимный режим."""
        if not (self.config.username and self.config.password):
            logger.debug(f"WordPress client создан БЕЗ авторизации для {self.base_url}")
            return {}

        auth_string = f"{self.config.username}:{self.config.password}"
        auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
        logger.debug(f"WordPress client создан с авторизацией для {self.base_url}")
        return {"Authorization": f"Basic {auth_b64}"}

    # ── HTTP глаголы ──────────────────────────────────────────────────

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        suppress_non_retryable_log: bool = False,
    ) -> Dict[str, Any]:
        """GET запрос с retry и circuit breaker."""
        logger.debug(f"GET {endpoint} params={params}")
        return await self.request_json(
            "GET",
            endpoint,
            params=params,
            suppress_non_retryable_log=suppress_non_retryable_log,
        )

    async def post(
        self,
        endpoint: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST запрос с retry и circuit breaker (json или multipart)."""
        if files:
            logger.debug(f"POST {endpoint} (multipart upload)")
            logger.debug(f"Multipart data keys: {list(data.keys()) if data else 'None'}")
            logger.debug(f"Multipart files keys: {list(files.keys())}")
            for key, file_info in files.items():
                logger.debug(
                    f"File '{key}': name={file_info[0]}, MIME={file_info[2]}, "
                    f"size={len(file_info[1])} bytes"
                )
            # Content-Type для multipart выставляет httpx сам — свой убираем.
            headers = {k: v for k, v in self._request_headers().items() if k.lower() != "content-type"}
            return await self.request_json("POST", endpoint, data=data, files=files, headers=headers)

        logger.debug(f"POST {endpoint} with full data: {data}")
        return await self.request_json("POST", endpoint, json=data)

    async def put(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """PUT запрос с retry и circuit breaker."""
        logger.debug(f"PUT {endpoint} with data keys: {list(data.keys())}")
        logger.debug(f"PUT {endpoint} with full data: {data}")
        return await self.request_json("PUT", endpoint, json=data)

    async def delete(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """DELETE запрос с retry и circuit breaker."""
        logger.debug(f"DELETE {endpoint} params={params}")
        return await self.request_json("DELETE", endpoint, params=params)

    async def upload_binary(
        self,
        endpoint: str,
        content: bytes,
        headers: Dict[str, str],
        parse: Callable[[httpx.Response], Dict[str, Any]],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Загрузить бинарные данные (медиа) тем же путём, что и остальные запросы:
        общий клиент, авторизация, retry и circuit breaker.

        `parse` — разбор ответа вместо обычного JSON-парсера: WordPress на
        некоторых хостингах отдаёт JSON после PHP warnings или только ID в
        заголовке, и это не ошибка (см. `MediaEndpoint._parse_upload_response`).
        """
        # Слияние — через httpx.Headers, без учёта регистра: у заголовков
        # клиента ключи строчные ('content-type': application/json), и в
        # обычном dict наш 'Content-Type' лёг бы рядом вторым значением —
        # WordPress брал первое и разбирал картинку как JSON (400).
        request_headers = httpx.Headers(self._request_headers())
        request_headers.update(headers)
        logger.debug(f"POST {endpoint} (binary upload, {len(content)} bytes)")
        # timeout=None в httpx означает «без таймаута», поэтому передаём его
        # только когда он действительно задан.
        extra = {"timeout": timeout} if timeout is not None else {}
        return await self.request_json(
            "POST",
            endpoint,
            content=content,
            headers=request_headers,
            parse=parse,
            **extra,
        )

    def _request_headers(self) -> Dict[str, str]:
        """Заголовки клиента (если он уже открыт) или собранные заново."""
        if self._client is not None:
            return dict(self._client.headers)
        return {**self.default_headers(), **self.auth_headers()}

    # ── Диагностика ───────────────────────────────────────────────────

    async def test_connection(self, authenticated: bool = False) -> bool:
        """
        Проверка подключения к WordPress API.

        Returns:
            True если подключение успешно

        Raises:
            AuthenticationError, NetworkError
        """
        try:
            if authenticated:
                await self.get("/wp-json/wp/v2/users/me")
                logger.info(f"✓ Авторизованное подключение к WordPress API успешно: {self.base_url}")
            else:
                await self.get("/wp-json/wp/v2/")
                logger.info(f"✓ Подключение к WordPress API успешно: {self.base_url}")
            return True
        except Exception as e:
            logger.error(f"✗ Ошибка подключения к WordPress API: {e}")
            raise
