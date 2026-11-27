"""
Базовый HTTP-провайдер.

Всё, что каждая интеграция иначе переизобретает по-своему, делается один раз здесь:

- 401 / 403                      → AuthenticationError (exit 3)
- 404                            → NotFoundError
- 429 / 502 / 503 / 504, таймауты,
  сетевые ошибки                 → ретраи с exponential backoff
                                   (с учётом заголовка Retry-After), затем
                                   ошибка провайдера (exit 5)
- прочие 4xx / 5xx               → WordPressError/ServerError со статусом
- не-JSON тело там, где ждали JSON → WordPressError
- User-Agent `wp-bulk/<version>` и конечный таймаут, чтобы зависший сайт
  не подвесил агента навсегда

Ретраи и circuit breaker берутся из `utils/retry.py` — там своя реализация,
знающая иерархию исключений проекта (см. AGENTS.md, не заменять на tenacity).

`transport` — тестовый шов для `httpx.MockTransport`; в боевом коде None.
"""

from typing import Any, Callable, Dict, Optional, cast

import httpx
from loguru import logger

from ..exceptions import (
    AuthenticationError,
    NetworkError,
    NotFoundError,
    ServerError,
    WordPressError,
)
from ..utils.retry import CircuitBreaker, RetryConfig, async_retry
from ..version import installed_version

# Статусы, которые значат «не сейчас», а не «нет» — их имеет смысл повторить.
RETRY_STATUSES = frozenset({429, 502, 503, 504})

DEFAULT_TIMEOUT = 30.0


def _user_agent() -> str:
    """User-Agent вида `wp-bulk/<version>`."""
    return f"wp-bulk/{installed_version() or 'dev'}"


class HttpProvider:
    """
    База для одной HTTP-интеграции.

    Подкласс задаёт `name`, base_url и `auth_headers()`, а методы запросов
    строит поверх `request_json()`.
    """

    name: str = "http"

    def __init__(
        self,
        base_url: str,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = base_url
        self.verify = verify
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.retry_config = retry_config or RetryConfig()
        self.circuit_breaker = circuit_breaker
        self.transport = transport
        self._client: Optional[httpx.AsyncClient] = None

        # Ретрай-обёртка строится на конфиге экземпляра, а не на дефолтном.
        self._request_with_retry = async_retry(self.retry_config)(self._request_once)

    # ── Жизненный цикл клиента ────────────────────────────────────────

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def auth_headers(self) -> Dict[str, str]:
        """Заголовки авторизации. Переопределяется подклассом."""
        return {}

    def default_headers(self) -> Dict[str, str]:
        """Базовые заголовки запроса (без авторизации)."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _user_agent(),
        }

    async def open(self):
        """Создать HTTP клиент (идемпотентно)."""
        if self._client is None:
            headers = {**self.default_headers(), **self.auth_headers()}
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                verify=self.verify,
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=True,
            )

    async def close(self):
        """Закрыть HTTP клиент."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug(f"{self.name}: HTTP клиент закрыт")

    # ── Маппинг ошибок ────────────────────────────────────────────────

    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[float]:
        """Значение Retry-After в секундах (HTTP-дату не поддерживаем)."""
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw.strip())
        except ValueError:
            return None

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """Сообщение об ошибке из JSON-тела, иначе первые 200 символов текста."""
        text = str(response.text)[:200]  # текстовый ответ сервера пригодится как fallback в любом случае
        try:
            body = response.json()
        except Exception:
            return text
        message = body.get("message") if isinstance(body, dict) else None
        return message if isinstance(message, str) else text

    def raise_for_status(self, response: httpx.Response):
        """Превратить ошибочный статус в исключение проекта."""
        status = response.status_code
        if status < 400:
            return

        retry_after = self._retry_after(response)

        if status in (401, 403):
            raise AuthenticationError(
                f"Ошибка аутентификации. Проверьте credentials. URL: {response.url}. {self._error_message(response)}",
                provider=self.name,
                status=status,
            )

        if status == 404:
            raise NotFoundError(
                f"Ресурс не найден: {response.url}",
                provider=self.name,
                status=status,
            )

        error: WordPressError
        if status >= 500:
            error = ServerError(
                f"Ошибка сервера WordPress ({status}): {response.text[:200]}",
                provider=self.name,
                status=status,
            )
        else:
            error = WordPressError(
                f"WordPress API error ({status}): {self._error_message(response)}",
                provider=self.name,
                status=status,
            )

        if status in RETRY_STATUSES and retry_after is not None:
            # Подхватывается async_retry как длительность паузы.
            error.retry_after = retry_after
        raise error

    def parse_json(self, response: httpx.Response, endpoint: str) -> Any:
        """Разобрать JSON-тело; не-JSON от JSON-ожидающего вызова — ошибка провайдера."""
        try:
            return response.json()
        except Exception:
            logger.error(
                f"Failed to parse JSON response from {endpoint}. "
                f"Status: {response.status_code}, "
                f"Content-Type: {response.headers.get('content-type')}, "
                f"Body: {response.text[:500]}"
            )
            raise WordPressError(
                f"WordPress API returned invalid JSON. Status: {response.status_code}, Response: {response.text[:200]}",
                provider=self.name,
                status=response.status_code,
            )

    # ── Отправка запроса ──────────────────────────────────────────────

    async def _request_once(
        self,
        method: str,
        endpoint: str,
        *,
        parse: Optional[Callable[[httpx.Response], Any]] = None,
        suppress_non_retryable_log: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Одна попытка запроса: отправить, проверить статус, разобрать ответ."""
        if self._client is None:
            await self.open()

        client = cast(httpx.AsyncClient, self._client)  # open() выше его создал
        try:
            response = await client.request(method, endpoint, **kwargs)
        except httpx.TimeoutException as e:
            raise NetworkError(f"Timeout: {e}", provider=self.name)
        except httpx.TransportError as e:
            raise NetworkError(f"Сетевая ошибка: {e}", provider=self.name)

        logger.debug(f"{method} {endpoint} -> {response.status_code}")
        self.raise_for_status(response)

        if parse is not None:
            return parse(response)
        return self.parse_json(response, endpoint)

    async def request_json(
        self,
        method: str,
        endpoint: str,
        *,
        parse: Optional[Callable[[httpx.Response], Any]] = None,
        suppress_non_retryable_log: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Запрос с ретраями и circuit breaker."""
        return cast(
            Dict[str, Any],
            await self._with_circuit_breaker(
                self._request_with_retry,
                method,
                endpoint,
                parse=parse,
                suppress_non_retryable_log=suppress_non_retryable_log,
                **kwargs,
            ),
        )

    async def _with_circuit_breaker(self, retry_fn: Callable, *args: Any, **kwargs: Any):
        """Вызвать retry-функцию через circuit breaker (если включён)."""
        if self.circuit_breaker:
            return await self.circuit_breaker.call(retry_fn, *args, **kwargs)
        return await retry_fn(*args, **kwargs)
