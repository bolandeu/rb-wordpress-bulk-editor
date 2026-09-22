"""
Tests for src/cli/doctor_commands.py — форма отчёта doctor (human и --json).
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app, doctor_commands
from src.credentials import manager
from src.utils.auth import load_credentials_from_env

runner = CliRunner()

EXPECTED_CHECKS = ["project", "cli_compat", "config", "credentials", "credential_store"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """
    Окружение без WP_*-переменных, без .env в cwd и без настоящего keychain —
    отчёт зависит только от кода, а не от машины, где идут тесты.
    """
    for name in ("WP_DOMAIN", "WP_API_USER", "WP_API_TOKEN", "WP_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(manager.keyring, "get_password", lambda service, user: None)
    monkeypatch.setattr(doctor_commands, "store_backend", lambda: "tests.FakeKeyring")


class TestDoctorOutput:
    def test_human_output_lists_every_check(self):
        result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        for name in EXPECTED_CHECKS:
            assert name in result.stdout
        assert "Итог" in result.stdout

    def test_json_envelope_shape(self):
        result = runner.invoke(app, ["doctor", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is True
        assert payload["status"] in {"ok", "warn", "fail"}
        assert [c["name"] for c in payload["checks"]] == EXPECTED_CHECKS
        for check in payload["checks"]:
            assert check["status"] in {"ok", "warn", "fail"}
            assert check["message"]

    def test_connection_check_is_opt_in(self):
        without = json.loads(runner.invoke(app, ["doctor", "--json"]).stdout.strip())
        assert "connection" not in [c["name"] for c in without["checks"]]

    def test_credentials_source_is_reported(self, monkeypatch):
        monkeypatch.setenv("WP_DOMAIN", "https://example.com")
        monkeypatch.setenv("WP_API_USER", "admin")
        monkeypatch.setenv("WP_API_TOKEN", "secret")

        payload = json.loads(runner.invoke(app, ["doctor", "--json"]).stdout.strip())
        creds = next(c for c in payload["checks"] if c["name"] == "credentials")

        assert creds["status"] == "ok"
        assert creds["details"]["sources"] == {
            "domain": "env",
            "username": "env",
            "password": "env",
        }
        # Пароль наружу не отдаётся, username — только префикс.
        assert "secret" not in json.dumps(payload, ensure_ascii=False)
        assert creds["details"]["username"] == "adm***"

    def test_env_file_in_cwd_is_seen(self, tmp_path):
        """
        Регрессия: doctor обязан видеть тот же `.env`, что и sync.

        Без проекта `.env` берётся из текущей папки — если doctor его не
        находит, он рапортует «credentials не разрешены» там, где реальная
        команда спокойно работает.
        """
        (tmp_path / ".env").write_text(
            "WP_DOMAIN=https://example.com\nWP_API_USER=admin\nWP_API_TOKEN=secret\n",
            encoding="utf-8",
        )

        payload = json.loads(runner.invoke(app, ["doctor", "--json"]).stdout.strip())
        creds = next(c for c in payload["checks"] if c["name"] == "credentials")

        assert creds["status"] == "ok"
        assert creds["details"]["domain"] == "https://example.com"
        assert set(creds["details"]["sources"].values()) == {".env"}
        # Та же цепочка, что у реальных команд, даёт тот же результат.
        assert load_credentials_from_env() == ("https://example.com", "admin", "secret")

    def test_credentials_from_cli_flags_win(self):
        payload = json.loads(
            runner.invoke(
                app,
                [
                    "doctor", "--json",
                    "--domain", "https://example.com",
                    "--username", "admin",
                    "--password", "secret",
                ],
            ).stdout.strip()
        )
        creds = next(c for c in payload["checks"] if c["name"] == "credentials")

        assert creds["status"] == "ok"
        assert set(creds["details"]["sources"].values()) == {"cli"}
