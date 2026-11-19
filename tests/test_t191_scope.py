"""
T191: wp-bulk сужен до импорта и экспорта через REST API WordPress.

- Удалённые команды (llm, counter, image-*, reshape, vlook) — «нет такой команды», exit 2.
- `describe --json` их не содержит.
- `auth status` не знает ключей LLM (llmkit больше не зависимость).
- `bulk-delete` без подтверждения под --non-interactive — exit 2 и ни одного
  DELETE-запроса к сайту (проверяется на уровне HTTP, без заглушек удаления).
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from rb_wordpress_bulk_editor.cli import app, tool_commands
from rb_wordpress_bulk_editor.wordpress.client import WordPressClient

runner = CliRunner()

REMOVED = ["llm", "counter", "image-crop", "image-compress", "image-convert", "reshape", "vlook"]


@pytest.mark.parametrize("command", REMOVED)
def test_removed_command_is_unknown(command):
    result = runner.invoke(app, [command])

    assert result.exit_code == 2
    assert "No such command" in result.output


def test_describe_has_no_removed_commands():
    result = runner.invoke(app, ["describe", "--json"])

    assert result.exit_code == 0
    names = {c["name"] for c in json.loads(result.stdout)["command"]["commands"]}
    assert names.isdisjoint(REMOVED)
    assert "bulk-delete" in names


def test_auth_status_has_no_llm_entries(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for var in ("WP_DOMAIN", "WP_API_USER", "WP_API_TOKEN", "SITE_DOMAIN"):
        monkeypatch.delenv(var, raising=False)

    result = runner.invoke(app, ["auth", "status", "--json"])

    providers = [e["provider"] for e in json.loads(result.stdout)["credentials"]]
    assert not [p for p in providers if p.startswith("llmkit")]


@pytest.fixture
def site(monkeypatch):
    """Сайт с двумя постами; запоминает все запросы."""
    requests: list[httpx.Request] = []
    posts = [
        {"id": 1, "title": {"rendered": "Первая"}, "featured_media": 0},
        {"id": 2, "title": {"rendered": "Вторая"}, "featured_media": 0},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(200, json=posts, headers={"X-WP-TotalPages": "1", "X-WP-Total": "2"})

    monkeypatch.setattr(
        tool_commands,
        "load_and_print_credentials",
        lambda **kwargs: ("https://example.com", "admin", "secret"),
    )
    monkeypatch.setattr(
        tool_commands,
        "WordPressClient",
        lambda config: WordPressClient(config, transport=httpx.MockTransport(handler)),
    )
    return requests


def _deletes(requests):
    return [r for r in requests if r.method == "DELETE"]


class TestBulkDeleteConfirmation:
    def test_non_interactive_without_confirm_sends_no_delete(self, site):
        result = runner.invoke(app, ["--non-interactive", "bulk-delete", "--post-type", "posts"])

        assert result.exit_code == 2
        assert _deletes(site) == []

    def test_non_interactive_with_confirm_deletes(self, site):
        result = runner.invoke(app, ["--non-interactive", "bulk-delete", "--post-type", "posts", "--confirm"])

        assert result.exit_code == 0, result.output
        assert [r.url.path for r in _deletes(site)] == ["/wp-json/wp/v2/posts/1", "/wp-json/wp/v2/posts/2"]
