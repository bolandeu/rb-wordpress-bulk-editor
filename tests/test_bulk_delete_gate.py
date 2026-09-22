"""
Tests for гейта подтверждения bulk-delete (--dry-run / --confirm / --non-interactive).

Сайт не трогаем: чтение списка и само удаление подменены заглушками —
проверяется только то, в каком порядке срабатывают проверки.
"""

import pytest
from typer.testing import CliRunner

from src.cli import app, tool_commands

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

    def test_non_interactive_without_confirm_fails(self, fake_wp):
        result = runner.invoke(
            app, ["--json", "bulk-delete", "--post-type", "posts", "--non-interactive"]
        )

        assert result.exit_code == 2
        assert '"INVALID_ARGUMENTS"' in result.stdout
        assert "--confirm" in result.stdout
        # До сайта дело не дошло: ни чтения, ни удаления.
        assert fake_wp["collected"] == 0
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
