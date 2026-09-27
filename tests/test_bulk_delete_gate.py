"""
Tests for гейта подтверждения bulk-delete (--dry-run / --confirm / --non-interactive).

Сайт не трогаем: чтение списка и само удаление подменены заглушками —
проверяется только то, в каком порядке срабатывают проверки.
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app, tool_commands
from src.version import installed_version

runner = CliRunner()

POSTS = [{"id": 1, "title": {"rendered": "Первая"}}, {"id": 2, "title": {"rendered": "Вторая"}}]


@pytest.fixture
def fake_wp(monkeypatch):
    """Подменяет credentials, чтение списка и удаление; запоминает, что вызвали."""
    calls = {"collected": 0, "deleted": []}

    monkeypatch.setattr(
        tool_commands,
        "load_and_print_credentials",
        lambda **kwargs: ("https://example.com", "admin", "secret"),
    )

    async def fake_collect(wp_config, post_type, status):
        calls["collected"] += 1
        return POSTS

    async def fake_delete(wp_config, all_posts, post_type, force, delete_media):
        calls["deleted"] = [p["id"] for p in all_posts]
        return {"found": len(all_posts), "deleted": len(all_posts), "media_deleted": 0, "errors": 0}

    monkeypatch.setattr(tool_commands, "_collect_posts", fake_collect)
    monkeypatch.setattr(tool_commands, "_run_bulk_delete", fake_delete)
    return calls


class TestBulkDeleteGate:
    def test_dry_run_short_circuits_before_confirmation(self, fake_wp):
        result = runner.invoke(app, ["bulk-delete", "--post-type", "posts", "--dry-run"])

        assert result.exit_code == 0
        assert fake_wp["collected"] == 1
        assert fake_wp["deleted"] == []
        assert "DRY-RUN" in result.stdout

    @pytest.mark.parametrize(
        "argv",
        [
            ["--json", "bulk-delete", "--post-type", "posts", "--non-interactive"],
            ["bulk-delete", "--post-type", "posts", "--non-interactive", "--json"],
        ],
        ids=["глобальный --json", "локальный --json"],
    )
    def test_non_interactive_without_confirm_fails(self, fake_wp, argv):
        result = runner.invoke(app, argv)

        assert result.exit_code == 2
        # stdout под --json — только конверт: он обязан парситься целиком.
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert "--confirm" in payload["error"]["action"]
        # До сайта дело не дошло: ни чтения, ни удаления.
        assert fake_wp["collected"] == 0
        assert fake_wp["deleted"] == []

    def test_json_stdout_is_envelope_only_on_success(self, fake_wp):
        result = runner.invoke(
            app,
            ["--json", "bulk-delete", "--post-type", "posts", "--non-interactive", "--confirm"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout.strip())
        assert payload == {
            "ok": True,
            "dry_run": False,
            "post_type": "posts",
            "status": "any",
            "force": False,
            "found": 2,
            "deleted": 2,
            "media_deleted": 0,
            "errors": 0,
            "cli_version": installed_version(),
        }

    def test_json_stdout_is_envelope_only_on_dry_run(self, fake_wp):
        result = runner.invoke(app, ["--json", "bulk-delete", "--post-type", "posts", "--dry-run"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is True
        assert payload["dry_run"] is True
        assert payload["found"] == 2
        assert payload["deleted"] == 0
        assert [p["id"] for p in payload["posts"]] == [1, 2]
        assert fake_wp["deleted"] == []

    def test_non_interactive_with_confirm_proceeds(self, fake_wp):
        result = runner.invoke(
            app,
            ["bulk-delete", "--post-type", "posts", "--non-interactive", "--confirm"],
        )

        assert result.exit_code == 0
        assert fake_wp["deleted"] == [1, 2]

    def test_confirm_alone_proceeds_without_prompt(self, fake_wp):
        result = runner.invoke(app, ["bulk-delete", "--post-type", "posts", "--confirm"])

        assert result.exit_code == 0
        assert fake_wp["deleted"] == [1, 2]

    def test_piped_run_without_confirm_does_not_delete(self, fake_wp):
        # stdin не терминал (CliRunner) — вопрос задать некому, удалять нельзя.
        result = runner.invoke(app, ["bulk-delete", "--post-type", "posts"])

        assert result.exit_code == 2
        assert fake_wp["deleted"] == []


class TestBulkDeleteEnvelope:
    def test_unexpected_error_is_an_envelope(self, fake_wp, monkeypatch):
        async def broken_collect(wp_config, post_type, status):
            raise RuntimeError("сломалось")

        monkeypatch.setattr(tool_commands, "_collect_posts", broken_collect)

        result = runner.invoke(app, ["bulk-delete", "--post-type", "posts", "--dry-run", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is False
        assert payload["error"]["code"] == "UNEXPECTED"
        assert "сломалось" in payload["error"]["message"]
