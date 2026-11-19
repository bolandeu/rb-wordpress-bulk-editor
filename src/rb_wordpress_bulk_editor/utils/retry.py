"""
Retry логика и Circuit Breaker для устойчивости к ошибкам API.

Включает:
- RetryConfig - конфигурация retry
- @async_retry - декоратор для автоматического retry
- CircuitBreaker - защита от каскадных ошибок
"""

import asyncio
import functools
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Optional, TypeVar
from loguru import logger

from ..exceptions import (
    NetworkError,
    ServerError,
    WordPressError,
    WPBulkEditorError,
    AuthenticationError,
    NotFoundError,
)

T = TypeVar("T")


class RetryConfig:
    """
    Конфигурация retry логики.

    Attributes:
        max_retries: Максимальное количество попыток (default: 3)
        base_delay: Начальная задержка в секундах (default: 1.0)
        max_delay: Максимальная задержка в секундах (default: 30.0)
        exponential_base: База для exponential backoff (default: 2.0)
    """

    def __init__(
        self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0, exponential_base: float = 2.0
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base


def is_retryable_error(exception: Exception) -> bool:
    """
    Определить, стоит ли retry для этой ошибки.

    Retry для:
    - NetworkError (timeout, connection refused)
    - ServerError (5xx ошибки)
    - 429 (Too Many Requests)
    - 502, 503 (Bad Gateway, Service Unavailable)

    НЕ retry для:
    - AuthenticationError (401)
    - NotFoundError (404)
    - Ошибки валидации (400)

    Args:
        exception: Исключение для проверки

    Returns:
        True если retry стоит делать, False иначе
    """
    # Retry для network и server errors
    if isinstance(exception, (NetworkError, ServerError)):
        return True

    # Retry для специфичных HTTP кодов
    if isinstance(exception, WordPressError):
        error_msg = str(exception).lower()
        # 429 - Rate limit
        # 502 - Bad Gateway
        # 503 - Service Unavailable
        # 504 - Gateway Timeout
        if any(code in error_msg for code in ["429", "502", "503", "504"]):
            return True

    # НЕ retry для authentication и not found
    if isinstance(exception, (AuthenticationError, NotFoundError)):
        return False

    # По умолчанию не retry
    return False


def async_retry(config: Optional[RetryConfig] = None):
    """
    Декоратор для автоматического retry с exponential backoff.

    Использование:
        @async_retry()
        async def my_function():
            # код который может упасть
            ...

        # Или с кастомной конфигурацией:
        @async_retry(RetryConfig(max_retries=5, base_delay=2.0))
        async def my_function():
            ...

    Args:
        config: Конфигурация retry. Если None, используется дефолтная.

    Returns:
        Decorator для async функций
    """
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        name = getattr(func, "__name__", repr(func))

        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(config.max_retries + 1):
                try:
                    return await func(*args, **kwargs)

                except Exception as e:
                    last_exception = e

                    # Проверяем можно ли retry
                    if not is_retryable_error(e):
                        # Для ошибки "превышение страниц" используем DEBUG (это нормальная ситуация при пагинации)
                        error_msg = str(e)
                        if kwargs.get("suppress_non_retryable_log"):
                            logger.debug(f"{name}: Suppressed non-retryable error: {e}")
                        elif (
                            "превышает количество имеющихся страниц" in error_msg
                            or "exceeds the number of pages" in error_msg.lower()
                        ):
                            logger.debug(f"{name}: Pagination limit reached: {e}")
                        else:
                            logger.error(f"{name}: Non-retryable error: {e}")
                        raise

                    # Если это последняя попытка - кидаем ошибку
                    if attempt == config.max_retries:
                        logger.error(f"{name}: Max retries ({config.max_retries}) exceeded: {e}")
                        raise

                    # Вычисляем delay с exponential backoff.
                    # Если сервер прислал Retry-After (провайдер кладёт его
                    # в атрибут исключения) — он имеет приоритет.
                    delay = min(config.base_delay * (config.exponential_base**attempt), config.max_delay)
                    retry_after = getattr(e, "retry_after", None)
                    if retry_after is not None:
                        # Retry-After тоже ограничен max_delay: сайт с
                        # `Retry-After: 3600` иначе подвесил бы CLI на час,
                        # и агент не смог бы этого прервать.
                        requested = float(retry_after)
                        if requested > config.max_delay:
                            logger.warning(
                                f"{name}: Retry-After={requested:.0f}s превышает "
                                f"max_delay={config.max_delay:.0f}s, ждём {config.max_delay:.0f}s"
                            )
                        delay = max(delay, min(requested, config.max_delay))

                    logger.warning(
                        f"{name}: Attempt {attempt + 1}/{config.max_retries + 1} "
                        f"failed: {e}. Retrying in {delay:.2f}s..."
                    )

                    # Ждем перед следующей попыткой
                    await asyncio.sleep(delay)

            # Не должны сюда попасть (попыток всегда ≥ 1), но на всякий случай
            raise last_exception if last_exception is not None else RuntimeError(f"{name}: нет попыток")

        return wrapper

    return decorator


class CircuitState(Enum):
    """
    Состояния circuit breaker.

    - CLOSED: Нормальная работа, запросы проходят
    - OPEN: Блокировка запросов, слишком много ошибок
    - HALF_OPEN: Проверка восстановления, пропускаем 1 запрос
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(WPBulkEditorError):
    """
    Circuit breaker открыт, запросы блокируются.

    Для CLI это отказ внешнего сервиса → PROVIDER_ERROR (exit 5),
    а не «неожиданная ошибка».
    """

    code = "PROVIDER_ERROR"
    exit_code = 5
    default_action = "Подождите окончания recovery-таймаута и повторите."


class CircuitBreaker:
    """
    Circuit breaker pattern для защиты от каскадных ошибок.

    Логика:
    1. CLOSED (нормальная работа):
       - Запросы проходят
       - Считаем consecutive_failures
       - Если consecutive_failures >= threshold → OPEN

    2. OPEN (блокировка):
       - Блокируем все запросы (кидаем CircuitBreakerOpenError)
       - После recovery_timeout → HALF_OPEN

    3. HALF_OPEN (проверка):
       - Пропускаем 1 запрос для проверки
       - Успех → CLOSED, сброс счетчика
       - Ошибка → OPEN снова

    Attributes:
        failure_threshold: Порог ошибок для открытия (default: 10)
        recovery_timeout: Время восстановления в секундах (default: 60)
    """

    def __init__(self, failure_threshold: int = 10, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: Optional[datetime] = None
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs):
        """
        Выполнить функцию через circuit breaker.

        Args:
            func: Async функция для вызова
            *args: Позиционные аргументы
            **kwargs: Именованные аргументы

        Returns:
            Результат функции

        Raises:
            CircuitBreakerOpenError: Если circuit breaker открыт
            Exception: Любое исключение из func
        """
        # Проверяем состояние и определяем режим выполнения
        is_half_open_probe = False
        async with self._lock:
            if self.state == CircuitState.OPEN:
                # Проверяем не пора ли перейти в HALF_OPEN
                if self.last_failure_time:
                    elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                    if elapsed >= self.recovery_timeout:
                        logger.info(
                            f"Circuit breaker: OPEN → HALF_OPEN (recovery timeout {self.recovery_timeout}s elapsed)"
                        )
                        self.state = CircuitState.HALF_OPEN
                        is_half_open_probe = True
                    else:
                        raise CircuitBreakerOpenError(
                            f"Circuit breaker OPEN. Consecutive failures: {self.consecutive_failures}. "
                            f"Retry in {self.recovery_timeout - elapsed:.1f}s"
                        )
            elif self.state == CircuitState.HALF_OPEN:
                is_half_open_probe = True

        # Выполняем функцию
        try:
            result = await func(*args, **kwargs)

            # Успех - сбрасываем счетчик
            async with self._lock:
                if is_half_open_probe:
                    logger.info("Circuit breaker: HALF_OPEN → CLOSED (successful request)")
                self.state = CircuitState.CLOSED
                self.consecutive_failures = 0
                self.last_failure_time = None

            return result

        except Exception as e:
            # Ошибка - увеличиваем счетчик
            async with self._lock:
                self.consecutive_failures += 1
                self.last_failure_time = datetime.now()

                if is_half_open_probe:
                    logger.warning(f"Circuit breaker: HALF_OPEN → OPEN (failed request: {e})")
                    self.state = CircuitState.OPEN

                elif self.consecutive_failures >= self.failure_threshold:
                    logger.error(f"Circuit breaker: CLOSED → OPEN ({self.consecutive_failures} consecutive failures)")
                    self.state = CircuitState.OPEN

            raise

    def reset(self):
        """
        Ручной сброс circuit breaker в CLOSED состояние.

        Полезно для тестов или ручного вмешательства.
        """
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = None
        logger.info("Circuit breaker: manually reset to CLOSED")

    def get_state(self) -> dict:
        """
        Получить текущее состояние circuit breaker.

        Returns:
            Словарь с состоянием:
            - state: текущее состояние (CLOSED/OPEN/HALF_OPEN)
            - consecutive_failures: количество последовательных ошибок
            - last_failure_time: время последней ошибки
        """
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
        }
