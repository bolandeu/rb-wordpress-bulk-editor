"""
Tests for src/providers/http.py и src/providers/wordpress.py
"""

import asyncio

import httpx
import pytest

from src.config import WordPressConfig
from src.exceptions import (
    AuthenticationError,
    NetworkError,
    NotFoundError,
    ServerError,
    WordPressError,
)
from src.providers.wordpress import WordPressHttpProvider
from src.utils.retry import CircuitBreaker, CircuitBreakerOpenError, CircuitState, RetryConfig
from src.wordpress.client import WordPressClient
from src.wordpress.media import MediaEndpoint


# ── Helpers ──────────────────────────────────────────────────────────

def make_config(url="https://example.com", username="admin", password="secret") -> WordPressConfig:
    return WordPressConfig(url=url, username=username, password=password)


def sequence_transport(responses: list, calls: list) -> httpx.MockTransport:
    """Отдаёт ответы по порядку, последний повторяется; пишет запросы в calls."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        index = min(len(calls) - 1, len(responses) - 1)
        return responses[index]

    return httpx.MockTransport(handler)


def make_provider(responses: list, calls: list, **kwargs) -> WordPressHttpProvider:
    kwargs.setdefault("retry_config", RetryConfig(max_retries=2, base_delay=0.01))
    return WordPressHttpProvider(
        make_config(),
        transport=sequence_transport(responses, calls),
        **kwargs,
    )


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Перехватить asyncio.sleep: тесты не ждут, но видят длительности пауз."""
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


# ── Маппинг статусов ─────────────────────────────────────────────────

class TestErrorMapping:
    async def test_401_raises_auth_required(self):
        calls = []
        provider = make_provider([httpx.Response(401, json={"message": "bad creds"})], calls)
        async with provider:
            with pytest.raises(AuthenticationError) as exc:
                await provider.get("/wp-json/wp/v2/posts")

        assert exc.value.code == "AUTH_REQUIRED"
        assert exc.value.exit_code == 3
        assert exc.value.status == 401
        assert len(calls) == 1  # не ретраится

    async def test_403_raises_auth_required(self):
        calls = []
        provider = make_provider([httpx.Response(403, json={"message": "forbidden"})], calls)
        async with provider:
            with pytest.raises(AuthenticationError):
                await provider.get("/wp-json/wp/v2/posts")

    async def test_404_raises_not_found(self):
        calls = []
        provider = make_provider([httpx.Response(404, json={"code": "rest_post_invalid_id"})], calls)
        async with provider:
            with pytest.raises(NotFoundError) as exc:
                await provider.get("/wp-json/wp/v2/posts/999")

        assert exc.value.status == 404
        assert len(calls) == 1

    async def test_400_raises_wordpress_error_with_status(self):
        calls = []
        provider = make_provider([httpx.Response(400, json={"message": "invalid param"})], calls)
        async with provider:
            with pytest.raises(WordPressError) as exc:
                await provider.post("/wp-json/wp/v2/posts", {"title": "x"})

        assert exc.value.code == "PROVIDER_ERROR"
        assert exc.value.exit_code == 5
        assert exc.value.to_dict()["status"] == 400

    async def test_non_json_body_is_provider_error(self):
        calls = []
        provider = make_provider([httpx.Response(200, text="<html>not json</html>")], calls)
        async with provider:
            with pytest.raises(WordPressError, match="invalid JSON"):
                await provider.get("/wp-json/wp/v2/posts")

    async def test_transport_error_becomes_network_error(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ConnectError("no route", request=request)

        provider = WordPressHttpProvider(
            make_config(),
            transport=httpx.MockTransport(handler),
            retry_config=RetryConfig(max_retries=1, base_delay=0.01),
        )
        async with provider:
            with pytest.raises(NetworkError):
                await provider.get("/wp-json/wp/v2/posts")

        assert len(calls) == 2  # первая попытка + один ретрай


# ── Ретраи ───────────────────────────────────────────────────────────

class TestRetries:
    async def test_429_honors_retry_after_header(self, recorded_sleeps):
        calls = []
        provider = make_provider(
            [
                httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "slow down"}),
                httpx.Response(200, json={"id": 1}),
            ],
            calls,
        )
        async with provider:
            result = await provider.get("/wp-json/wp/v2/posts")

        assert result == {"id": 1}
        assert len(calls) == 2
        assert recorded_sleeps == [7.0]

    async def test_retry_after_is_capped_by_max_delay(self, recorded_sleeps):
        """`Retry-After: 3600` не должен парковать CLI на час."""
        calls = []
        provider = make_provider(
            [
                httpx.Response(429, headers={"Retry-After": "3600"}, json={"message": "slow down"}),
                httpx.Response(200, json={"id": 1}),
            ],
            calls,
            retry_config=RetryConfig(max_retries=1, base_delay=0.01, max_delay=5.0),
        )
        async with provider:
            await provider.get("/wp-json/wp/v2/posts")

        assert recorded_sleeps == [5.0]

    async def test_429_without_header_uses_exponential_backoff(self, recorded_sleeps):
        calls = []
        provider = make_provider(
            [httpx.Response(429, json={"message": "slow down"}), httpx.Response(200, json={"id": 1})],
            calls,
        )
        async with provider:
            await provider.get("/wp-json/wp/v2/posts")

        assert recorded_sleeps == [0.01]

    async def test_5xx_retried_then_provider_error(self, recorded_sleeps):
        calls = []
        provider = make_provider([httpx.Response(500, text="boom")], calls)
        async with provider:
            with pytest.raises(ServerError) as exc:
                await provider.get("/wp-json/wp/v2/posts")

        assert len(calls) == 3  # первая попытка + 2 ретрая
        assert exc.value.exit_code == 5

    async def test_503_recovers_after_retry(self, recorded_sleeps):
        calls = []
        provider = make_provider(
            [httpx.Response(503, text="maintenance"), httpx.Response(200, json={"ok": 1})],
            calls,
        )
        async with provider:
            assert await provider.get("/wp-json/wp/v2/") == {"ok": 1}


# ── Circuit breaker ──────────────────────────────────────────────────

class TestCircuitBreaker:
    async def test_opens_after_threshold_and_blocks(self, recorded_sleeps):
        calls = []
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        provider = make_provider(
            [httpx.Response(500, text="boom")],
            calls,
            retry_config=RetryConfig(max_retries=0),
            circuit_breaker=breaker,
        )
        async with provider:
            with pytest.raises(ServerError):
                await provider.get("/wp-json/wp/v2/posts")

            assert breaker.state == CircuitState.OPEN

            with pytest.raises(CircuitBreakerOpenError) as exc:
                await provider.get("/wp-json/wp/v2/posts")

        assert len(calls) == 1  # второй запрос до сети не дошёл
        assert exc.value.code == "PROVIDER_ERROR"
        assert exc.value.exit_code == 5

    async def test_half_open_recovers_on_success(self):
        calls = []
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        provider = make_provider(
            [httpx.Response(500, text="boom"), httpx.Response(200, json={"ok": True})],
            calls,
            retry_config=RetryConfig(max_retries=0),
            circuit_breaker=breaker,
        )
        async with provider:
            with pytest.raises(ServerError):
                await provider.get("/wp-json/wp/v2/posts")

            await asyncio.sleep(0.1)

            assert await provider.get("/wp-json/wp/v2/posts") == {"ok": True}

        assert breaker.state == CircuitState.CLOSED


# ── Клиент поверх провайдера ─────────────────────────────────────────

class TestClientOverProvider:
    async def test_transport_seam_on_client(self):
        calls = []
        client = WordPressClient(
            make_config(),
            transport=sequence_transport([httpx.Response(200, json=[{"id": 7}])], calls),
        )
        async with client:
            result = await client.get("/wp-json/wp/v2/posts")

        assert result[0]["id"] == 7

    async def test_auth_header_reaches_request(self):
        calls = []
        client = WordPressClient(
            WordPressConfig(url="https://example.com", username="user", password="pass"),
            transport=sequence_transport([httpx.Response(200, json={})], calls),
        )
        async with client:
            await client.get("/wp-json/wp/v2/")

        assert calls[0].headers["Authorization"].startswith("Basic ")
        assert calls[0].headers["User-Agent"].startswith("wp-bulk/")


# ── Загрузка медиа через провайдер ───────────────────────────────────

class TestMediaUpload:
    async def test_upload_goes_through_provider_with_auth(self):
        calls = []
        client = WordPressClient(
            make_config(),
            transport=sequence_transport([httpx.Response(201, json={"id": 11, "source_url": "u"})], calls),
        )
        async with client:
            media = MediaEndpoint(client)
            result = await media._upload_data(b"data", "pic.jpg", "image/jpeg")

        assert result["id"] == 11
        assert calls[0].headers["Authorization"].startswith("Basic ")
        assert calls[0].headers["Content-Disposition"] == 'attachment; filename="pic.jpg"'

    async def test_upload_tolerates_php_warnings_before_json(self):
        """PHP-хостинг подмешал warning перед JSON — это не ошибка провайдера."""
        calls = []
        body = "<br />\nWarning: something in /var/www on line 1\n{\"id\": 42, \"source_url\": \"u\"}"
        client = WordPressClient(
            make_config(),
            transport=sequence_transport([httpx.Response(201, text=body)], calls),
        )
        async with client:
            media = MediaEndpoint(client)
            result = await media._upload_data(b"data", "pic.jpg", "image/jpeg")

        assert result["id"] == 42

    async def test_upload_falls_back_to_id_header(self):
        calls = []
        client = WordPressClient(
            make_config(),
            transport=sequence_transport(
                [httpx.Response(201, text="warning only", headers={"X-WP-Upload-Attachment-ID": "77"})],
                calls,
            ),
        )
        async with client:
            media = MediaEndpoint(client)
            result = await media._upload_data(b"data", "pic.jpg", "image/jpeg")

        assert result["id"] == 77

    async def test_upload_401_maps_to_auth_required(self):
        calls = []
        client = WordPressClient(
            make_config(),
            transport=sequence_transport([httpx.Response(401, json={"message": "nope"})], calls),
        )
        async with client:
            media = MediaEndpoint(client)
            with pytest.raises(AuthenticationError):
                await media._upload_data(b"data", "pic.jpg", "image/jpeg")
