"""
Tests for src/wordpress/client.py
"""

import base64
import json
import pytest
import httpx

from src.wordpress.client import WordPressClient
from src.config import WordPressConfig
from src.exceptions import AuthenticationError, NotFoundError, ServerError, NetworkError


# ── Helpers ──────────────────────────────────────────────────────────

def make_config(url="https://example.com", username="admin", password="secret") -> WordPressConfig:
    return WordPressConfig(url=url, username=username, password=password)


def mock_transport(responses: dict):
    """
    Простой MockTransport: responses = {method_url_key: httpx.Response}.
    Ключ: "METHOD URL", например "GET https://example.com/wp-json/wp/v2/posts".
    """
    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url}"
        if key in responses:
            return responses[key]
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


# ── Auth headers ──────────────────────────────────────────────────────

class TestAuthHeaders:
    async def test_basic_auth_header_set(self):
        config = make_config(username="user", password="pass")
        client = WordPressClient(config)
        await client.open()

        expected_b64 = base64.b64encode(b"user:pass").decode("ascii")
        assert client._client.headers["Authorization"] == f"Basic {expected_b64}"
        await client.close()

    async def test_no_auth_header_when_no_credentials(self):
        config = WordPressConfig(url="https://example.com")
        client = WordPressClient(config)
        await client.open()

        assert "Authorization" not in client._client.headers
        await client.close()

    async def test_password_spaces_stripped(self):
        """Application Password часто копируют с пробелами."""
        config = make_config(password="ab cd ef gh")
        # password validator убирает пробелы
        assert config.password == "abcdefgh"


# ── GET requests ──────────────────────────────────────────────────────

class TestGet:
    async def test_get_returns_parsed_json(self):
        config = make_config()
        transport = mock_transport({
            "GET https://example.com/wp-json/wp/v2/posts": httpx.Response(
                200, json=[{"id": 1, "title": {"rendered": "Hello"}}]
            )
        })
        async with WordPressClient(config) as client:
            client._client = httpx.AsyncClient(
                base_url="https://example.com",
                transport=transport,
                follow_redirects=True,
            )
            result = await client.get("/wp-json/wp/v2/posts")

        assert isinstance(result, list)
        assert result[0]["id"] == 1

    async def test_get_raises_not_found(self):
        config = make_config()
        transport = mock_transport({
            "GET https://example.com/wp-json/wp/v2/posts/999": httpx.Response(404, json={"code": "rest_post_invalid_id"})
        })
        async with WordPressClient(config) as client:
            client._client = httpx.AsyncClient(
                base_url="https://example.com",
                transport=transport,
                follow_redirects=True,
            )
            with pytest.raises(NotFoundError):
                await client.get("/wp-json/wp/v2/posts/999")

    async def test_get_raises_auth_error(self):
        config = make_config()
        transport = mock_transport({
            "GET https://example.com/wp-json/wp/v2/posts": httpx.Response(401, json={"code": "rest_forbidden"})
        })
        async with WordPressClient(config) as client:
            client._client = httpx.AsyncClient(
                base_url="https://example.com",
                transport=transport,
                follow_redirects=True,
            )
            with pytest.raises(AuthenticationError):
                await client.get("/wp-json/wp/v2/posts")

    async def test_get_raises_server_error(self):
        config = make_config()
        transport = mock_transport({
            "GET https://example.com/wp-json/wp/v2/posts": httpx.Response(500, text="Internal Server Error")
        })
        async with WordPressClient(config) as client:
            client._client = httpx.AsyncClient(
                base_url="https://example.com",
                transport=transport,
                follow_redirects=True,
            )
            with pytest.raises(ServerError):
                await client.get("/wp-json/wp/v2/posts")


# ── POST requests ─────────────────────────────────────────────────────

class TestPost:
    async def test_post_returns_created_resource(self):
        config = make_config()
        transport = mock_transport({
            "POST https://example.com/wp-json/wp/v2/posts": httpx.Response(
                201, json={"id": 42, "status": "publish"}
            )
        })
        async with WordPressClient(config) as client:
            client._client = httpx.AsyncClient(
                base_url="https://example.com",
                transport=transport,
                follow_redirects=True,
            )
            result = await client.post("/wp-json/wp/v2/posts", {"title": "New Post"})

        assert result["id"] == 42


# ── Config validation ─────────────────────────────────────────────────

class TestConfigValidation:
    def test_url_without_scheme_gets_https(self):
        config = WordPressConfig(url="example.com")
        assert config.url == "https://example.com"

    def test_url_trailing_slash_removed(self):
        config = WordPressConfig(url="https://example.com/")
        assert config.url == "https://example.com"

    def test_url_with_wp_json_raises(self):
        with pytest.raises(Exception, match="wp-json"):
            WordPressConfig(url="https://example.com/wp-json/wp/v2")

    def test_url_with_http_preserved(self):
        config = WordPressConfig(url="http://local.test")
        assert config.url == "http://local.test"
