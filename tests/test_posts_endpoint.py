"""
Tests for src/wordpress/posts.py
"""

from src.wordpress.posts import FETCH_ALL_STATUSES, PostsEndpoint
from src.exceptions import WordPressError


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, endpoint, params=None, **kwargs):
        self.calls.append((endpoint, params or {}))
        status = (params or {}).get("status")
        context = (params or {}).get("context")
        page = (params or {}).get("page", 1)
        response = self.responses.get((status, context, page), self.responses.get((status, page), []))
        if isinstance(response, Exception):
            raise response
        return response


class TestPostsEndpoint:
    async def test_get_all_any_fetches_rest_compatible_statuses(self):
        client = FakeClient(
            {
                ("publish", 1): [{"id": 1, "status": "publish"}],
                ("draft", 1): [{"id": 2, "status": "draft"}],
                ("pending", 1): [],
                ("private", 1): [{"id": 3, "status": "private"}],
                ("future", 1): [],
            }
        )
        endpoint = PostsEndpoint(client, "posts")

        result = await endpoint.get_all(status="any")

        assert [post["id"] for post in result] == [1, 2, 3]
        called_statuses = [params.get("status") for _, params in client.calls]
        assert called_statuses == list(FETCH_ALL_STATUSES)
        assert "any" not in called_statuses
        assert all(params.get("context") is None for _, params in client.calls)

    async def test_get_all_retries_status_with_edit_context_when_needed(self):
        client = FakeClient(
            {
                ("publish", None, 1): WordPressError("WordPress API error (400): Неверный параметр: status"),
                ("publish", "edit", 1): [{"id": 1, "status": "publish"}],
            }
        )
        endpoint = PostsEndpoint(client, "rb_review")

        result = await endpoint.get_all(status="publish")

        assert result == [{"id": 1, "status": "publish"}]
        assert client.calls[0][1] == {"per_page": 100, "page": 1, "status": "publish"}
        assert client.calls[1][1] == {
            "per_page": 100,
            "page": 1,
            "context": "edit",
            "status": "publish",
        }

    async def test_list_omits_any_status_param(self):
        client = FakeClient({(None, 1): [{"id": 1}]})
        endpoint = PostsEndpoint(client, "posts")

        result = await endpoint.list(status="any")

        assert result == [{"id": 1}]
        assert "status" not in client.calls[0][1]
