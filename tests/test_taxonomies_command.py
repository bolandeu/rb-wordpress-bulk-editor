"""
Tests for команды `taxonomies`: список таксономий сайта для WP_TAXONOMIES.

Сайт подменён: discovery возвращает готовый список slug-ов.
"""

import json

import pytest
from typer.testing import CliRunner

from rb_wordpress_bulk_editor.cli import app, tool_commands

runner = CliRunner()


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(
        tool_commands,
        "load_and_print_credentials",
        lambda **kwargs: ("https://example.com", "admin", "secret"),
    )
    state = {"slugs": ["categories", "tags", "rb_services_cat", "brand"], "include_system": None}

    async def fake_discover(wp_config, include_system):
        state["include_system"] = include_system
        return state["slugs"]

    monkeypatch.setattr(tool_commands, "_discover_taxonomy_slugs", fake_discover)
    return state


class TestTaxonomiesCommand:
    @pytest.mark.parametrize("position", ["global", "local"])
    def test_json_envelope(self, site, position):
        args = ["--json", "taxonomies"] if position == "global" else ["taxonomies", "--json"]

        result = runner.invoke(app, args)

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is True and payload["cli_version"]
        assert payload["taxonomies"] == ["categories", "tags", "rb_services_cat", "brand"]
        assert payload["env_line"] == "WP_TAXONOMIES=categories,tags,rb_services_cat,brand"
        # sync берёт таксономии с сайта сам — отслеживать «что вписать» не нужно
        assert "needs_env" not in payload

    def test_human_output_has_env_line(self, site):
        result = runner.invoke(app, ["taxonomies"])

        assert result.exit_code == 0
        assert "WP_TAXONOMIES=categories,tags,rb_services_cat,brand" in result.stdout
        assert "brand" in result.stdout

    def test_empty_site(self, site):
        site["slugs"] = []

        result = runner.invoke(app, ["--json", "taxonomies"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout.strip())
        assert payload["taxonomies"] == [] and payload["env_line"] is None

    def test_include_system_passed_through(self, site):
        runner.invoke(app, ["--json", "taxonomies", "--include-system"])

        assert site["include_system"] is True

    def test_missing_credentials_is_an_envelope(self, monkeypatch):
        from rb_wordpress_bulk_editor.exceptions import AuthenticationError

        def no_credentials(**kwargs):
            raise AuthenticationError("нет доступа")

        monkeypatch.setattr(tool_commands, "load_and_print_credentials", no_credentials)

        result = runner.invoke(app, ["--json", "taxonomies"])

        assert result.exit_code == 3
        assert json.loads(result.stdout.strip())["ok"] is False
