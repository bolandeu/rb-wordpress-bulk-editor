"""
Tests for WP_BULK_MIN_VERSION: объявление в .env проекта и его проверка.
"""

import json
import sys

import pytest
from typer.testing import CliRunner

from src import cli
from src.cli import app, project as project_module
from src.cli.project import ProjectContext, check_cli_compat
from src.exceptions import CliTooOld

runner = CliRunner()


def make_project(tmp_path, env_body: str = "") -> ProjectContext:
    root = tmp_path / "mysite"
    root.mkdir()
    if env_body:
        (root / ".env").write_text(env_body, encoding="utf-8")
    return ProjectContext(name="mysite", root=root)


class TestMinVersionDeclaration:
    def test_read_from_project_env(self, tmp_path):
        project = make_project(tmp_path, "WP_BULK_MIN_VERSION=1.2.0\nWP_DOMAIN=https://x.com\n")
        assert project.min_version == "1.2.0"

    def test_absent_when_not_declared(self, tmp_path):
        assert make_project(tmp_path, "WP_DOMAIN=https://x.com\n").min_version is None

    def test_absent_without_env_file(self, tmp_path):
        assert make_project(tmp_path).min_version is None


class TestCheckCliCompat:
    def test_older_installed_version_raises(self, tmp_path):
        project = make_project(tmp_path, "WP_BULK_MIN_VERSION=1.2.0\n")

        with pytest.raises(CliTooOld) as exc:
            check_cli_compat(project, version="1.1.9")

        assert exc.value.exit_code == 6
        assert exc.value.code == "CLI_TOO_OLD"
        assert "uv tool upgrade" in exc.value.action

    @pytest.mark.parametrize("version", ["1.2.0", "1.2.1", "2.0.0", "1.10.0"])
    def test_equal_or_newer_passes(self, tmp_path, version):
        check_cli_compat(make_project(tmp_path, "WP_BULK_MIN_VERSION=1.2.0\n"), version=version)

    def test_no_requirement_passes(self, tmp_path):
        check_cli_compat(make_project(tmp_path, "WP_DOMAIN=https://x.com\n"), version="0.1.0")

    def test_source_run_without_installed_version_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(project_module, "installed_version", lambda: None)
        check_cli_compat(make_project(tmp_path, "WP_BULK_MIN_VERSION=9.9.9\n"))


class TestCliEnforcement:
    @pytest.fixture
    def project_env(self, tmp_path, monkeypatch):
        """Активный проект с объявленным минимумом версии."""
        project = make_project(tmp_path, "WP_BULK_MIN_VERSION=2.0.0\n")
        monkeypatch.setattr(cli, "resolve_project", lambda name: project)
        return project

    @pytest.mark.parametrize(
        "argv",
        [
            ["--json", "--project", "mysite", "validate"],
            ["--project", "mysite", "validate", "--json"],
        ],
        ids=["глобальный --json", "локальный --json"],
    )
    def test_too_old_cli_fails_command_with_exit_6(self, project_env, monkeypatch, argv):
        monkeypatch.setattr(project_module, "installed_version", lambda: "1.0.0")
        # Локальную форму callback видит только через argv — подменяем его.
        monkeypatch.setattr(sys, "argv", ["wp-bulk", *argv])

        result = runner.invoke(app, argv)

        assert result.exit_code == 6
        payload = json.loads(result.stdout.strip())
        assert payload["error"]["code"] == "CLI_TOO_OLD"
        assert "uv tool upgrade" in payload["error"]["action"]

    def test_doctor_reports_instead_of_failing(self, project_env, monkeypatch):
        monkeypatch.setattr(project_module, "installed_version", lambda: "1.0.0")

        result = runner.invoke(app, ["--json", "--project", "mysite", "doctor"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout.strip())
        compat = next(c for c in payload["checks"] if c["name"] == "cli_compat")
        assert compat["status"] == "fail"
        assert compat["details"] == {
            "installed": "1.0.0",
            "required": "2.0.0",
            "package": "wp-bulk-editor",
        }

    def test_new_enough_cli_passes_the_gate(self, project_env, monkeypatch):
        monkeypatch.setattr(project_module, "installed_version", lambda: "2.1.0")

        result = runner.invoke(app, ["--json", "--project", "mysite", "doctor"])

        payload = json.loads(result.stdout.strip())
        compat = next(c for c in payload["checks"] if c["name"] == "cli_compat")
        assert compat["status"] == "ok"
