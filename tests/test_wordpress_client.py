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
            with pytest.raises(AuthenticationError, match="URL: https://example.com/wp-json/wp/v2/posts"):
                await client.get("/wp-json/wp/v2/posts")

    async def test_authenticated_connection_checks_current_user(self):
        config = make_config()
        transport = mock_transport({
            "GET https://example.com/wp-json/wp/v2/users/me": httpx.Response(200, json={"id": 1})
        })
        async with WordPressClient(config) as client:
            client._client = httpx.AsyncClient(
                base_url="https://example.com",
                transport=transport,
                follow_redirects=True,
            )
            assert await client.test_connection(authenticated=True) is True

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


# ── Загрузка медиа ────────────────────────────────────────────────────

class TestMediaUploadHeaders:
    async def test_single_content_type_is_the_file_mime(self):
        """
        Регрессия: заголовки клиента несут 'content-type: application/json'
        строчными, и при слиянии обычным dict наш 'Content-Type' уходил вторым
        значением — WordPress разбирал JPEG как JSON и отвечал 400.
        """
        from src.wordpress.media import MediaEndpoint

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"id": 7, "source_url": "https://example.com/a.jpg"})

        async with WordPressClient(make_config(), transport=httpx.MockTransport(handler)) as client:
            result = await MediaEndpoint(client)._upload_data(b"\xff\xd8", "a.jpg", "image/jpeg")

        assert result["id"] == 7
        [request] = seen
        assert request.headers.get_list("content-type") == ["image/jpeg"]
        assert request.headers.get_list("content-disposition") == ['attachment; filename="a.jpg"']
        assert request.headers["authorization"].startswith("Basic ")
        assert request.content == b"\xff\xd8"
