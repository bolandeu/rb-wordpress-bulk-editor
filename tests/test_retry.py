"""
Tests for src/utils/retry.py
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.exceptions import (
    NetworkError,
    ServerError,
    AuthenticationError,
    NotFoundError,
    WordPressError,
)
from src.utils.retry import (
    is_retryable_error,
    async_retry,
    RetryConfig,
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenError,
)


# ── is_retryable_error ──────────────────────────────────────────────


class TestIsRetryableError:
    def test_network_error_retryable(self):
        assert is_retryable_error(NetworkError("timeout")) is True

    def test_server_error_retryable(self):
        assert is_retryable_error(ServerError("500")) is True

    def test_auth_error_not_retryable(self):
        assert is_retryable_error(AuthenticationError("401")) is False

    def test_not_found_not_retryable(self):
        assert is_retryable_error(NotFoundError("404")) is False

    @pytest.mark.parametrize("code", ["429", "502", "503", "504"])
    def test_wp_error_with_retryable_codes(self, code):
        assert is_retryable_error(WordPressError(f"HTTP {code} error")) is True

    def test_wp_error_with_400_not_retryable(self):
        assert is_retryable_error(WordPressError("HTTP 400 Bad Request")) is False

    def test_generic_exception_not_retryable(self):
        assert is_retryable_error(ValueError("oops")) is False


# ── @async_retry ────────────────────────────────────────────────────


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        call_count = 0

        @async_retry(RetryConfig(max_retries=3, base_delay=0.01))
        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        call_count = 0

        @async_retry(RetryConfig(max_retries=3, base_delay=0.01))
        async def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise NetworkError("timeout")
            return "recovered"

        result = await fail_twice()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        call_count = 0

        @async_retry(RetryConfig(max_retries=3, base_delay=0.01))
        async def auth_fail():
            nonlocal call_count
            call_count += 1
            raise AuthenticationError("401 Unauthorized")

        with pytest.raises(AuthenticationError):
            await auth_fail()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        call_count = 0

        @async_retry(RetryConfig(max_retries=2, base_delay=0.01))
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise NetworkError("connection refused")

        with pytest.raises(NetworkError):
            await always_fail()
        assert call_count == 3  # initial + 2 retries


# ── CircuitBreaker ──────────────────────────────────────────────────


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_closed_passes_through(self):
        cb = CircuitBreaker(failure_threshold=3)
        result = await cb.call(AsyncMock(return_value="ok"))
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_closed_to_open_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        failing = AsyncMock(side_effect=NetworkError("fail"))

        for _ in range(3):
            with pytest.raises(NetworkError):
                await cb.call(failing)

        assert cb.state == CircuitState.OPEN
        assert cb.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_open_blocks_requests(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)
        failing = AsyncMock(side_effect=NetworkError("fail"))

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(NetworkError):
                await cb.call(failing)

        assert cb.state == CircuitState.OPEN

        # Now it should block
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(AsyncMock(return_value="should not reach"))

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        failing = AsyncMock(side_effect=NetworkError("fail"))

        for _ in range(2):
            with pytest.raises(NetworkError):
                await cb.call(failing)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery
        await asyncio.sleep(0.1)

        # Next call should transition to HALF_OPEN then CLOSED on success
        result = await cb.call(AsyncMock(return_value="recovered"))
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        failing = AsyncMock(side_effect=NetworkError("fail"))

        for _ in range(2):
            with pytest.raises(NetworkError):
                await cb.call(failing)

        await asyncio.sleep(0.1)

        # Probe fails → back to OPEN
        with pytest.raises(NetworkError):
            await cb.call(failing)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_reset(self):
        cb = CircuitBreaker(failure_threshold=2)
        failing = AsyncMock(side_effect=NetworkError("fail"))

        for _ in range(2):
            with pytest.raises(NetworkError):
                await cb.call(failing)

        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0
        assert cb.last_failure_time is None

    @pytest.mark.asyncio
    async def test_get_state(self):
        cb = CircuitBreaker()
        state = cb.get_state()
        assert state["state"] == "closed"
        assert state["consecutive_failures"] == 0
        assert state["last_failure_time"] is None
