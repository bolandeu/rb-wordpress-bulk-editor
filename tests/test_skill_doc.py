"""
Tests for бандленного SKILL.md и флага --ai.
"""

from typer.testing import CliRunner

from rb_wordpress_bulk_editor.cli import app
from rb_wordpress_bulk_editor.data import read_skill_doc

runner = CliRunner()


def test_skill_doc_is_readable_from_package():
    doc = read_skill_doc()
    assert doc.startswith("# wp-bulk")
    assert "Exit-коды" in doc


def test_ai_flag_prints_the_bundled_doc():
    result = runner.invoke(app, ["--ai"])

    assert result.exit_code == 0
    assert result.stdout.strip() == read_skill_doc().strip()


def test_skill_doc_names_destructive_commands_but_not_the_command_list():
    """
    Соглашения — да, перечень команд — нет: список печатает голый `wp-bulk`.
    Из команд в документе упоминаются только опасные (этого требует протокол).
    """
    doc = read_skill_doc()
    assert "bulk-delete" in doc
    for command in ("wp-bulk fetch", "wp-bulk sync", "wp-bulk validate", "wp-bulk options"):
        assert command not in doc
