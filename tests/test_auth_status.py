"""
`wp-bulk auth status` — форма отчёта §8 (общий контракт инструментов) и exit-коды.

Форму читает `tools-watch`, не зная провайдеров: поменялась форма — это
ломающее изменение, и этот тест обязан упасть.
"""

import json
import os

import httpx
import pytest
from typer.testing import CliRunner

from src.cli import app
from src.config import WordPressConfig
from src.credentials import health, manager
from src.providers.wordpress import check_credentials
from tests.test_credentials import FakeKeyring

ENTRY_KEYS = {
    "provider",
    "required",
    "fields",
    "source",
    "source_ref",
    "status",
    "expires_at",
    "quota",
    "message",
}
REPORT_KEYS = {"ok", "tool", "version", "checked_at", "credentials", "cli_version"}


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(manager.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(manager.keyring, "set_password", fake.set_password)
    monkeypatch.setattr(manager.keyring, "delete_password", fake.delete_password)
    return fake


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, fake_keyring):
    """Ни настоящего окружения, ни .env рядом."""
    saved = dict(os.environ)  # load_dotenv пишет в окружение мимо monkeypatch
    for name in list(os.environ):
        if name.startswith(("WP_", "SITE_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _run(*args):
    result = CliRunner().invoke(app, ["auth", "status", *args, "--json"])
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def _checker(status, message=None):
    return lambda domain, username, password: (status, message)


class TestShape:
    def test_no_targets_reports_optional_wp_missing(self):
        code, payload = _run()
        assert code == 0
        assert set(payload) == REPORT_KEYS
        assert payload["ok"] is True and payload["tool"] == "wp-bulk"
        assert payload["checked_at"].endswith("Z")
        [entry] = payload["credentials"]
        assert set(entry) == ENTRY_KEYS
        assert entry["provider"] == "wp" and entry["required"] is False
        assert entry["status"] == "missing" and entry["source"] == "missing"

    def test_stored_target_one_entry_each_without_values(self):
        manager.store_credential("example.com", {"username": "editor", "password": "s3cret-pass"})
        manager.store_credential("b.example.org", {"username": "u2", "password": "p2"})
        code, payload = _run()
        assert code == 0
        providers = [e["provider"] for e in payload["credentials"]]
        assert providers == ["wp:b.example.org", "wp:example.com"]
        entry = payload["credentials"][1]
        assert entry["required"] is True
        assert entry["source"] == "keychain" and entry["source_ref"] == "keychain service wp-bulk:example.com"
        assert entry["status"] == "unchecked"
        assert "s3cret-pass" not in json.dumps(payload) and "editor" not in json.dumps(payload)

    def test_env_overriding_keychain_is_env_naming_both(self, monkeypatch):
        manager.store_credential("example.com", {"username": "editor", "password": "old"})
        monkeypatch.setenv("WP_EXAMPLE_COM_API_TOKEN", "new")
        _, payload = _run()
        [entry] = payload["credentials"]
        assert entry["source"] == "env"
        assert entry["source_ref"] == "keychain service wp-bulk:example.com; WP_EXAMPLE_COM_API_TOKEN"

    def test_logout_removes_target_from_report(self):
        manager.store_credential("example.com", {"username": "u", "password": "p"})
        manager.delete_credential("example.com")
        _, payload = _run()
        assert [e["provider"] for e in payload["credentials"]] == ["wp"]


class TestCheck:
    def test_invalid_is_exit_3_with_full_report(self, monkeypatch):
        manager.store_credential("example.com", {"username": "u", "password": "topsecret"})
        monkeypatch.setattr(health, "check_wordpress", _checker("invalid", "rejected topsecret"))
        code, payload = _run("--check")
        assert code == 3
        assert payload["ok"] is False and payload["error"]["code"] == "CREDENTIALS_UNHEALTHY"
        [entry] = payload["credentials"]
        assert entry["status"] == "invalid"
        assert entry["message"] == "rejected ***"

    def test_ok_is_exit_0(self, monkeypatch):
        manager.store_credential("example.com", {"username": "u", "password": "p"})
        monkeypatch.setattr(health, "check_wordpress", _checker("ok"))
        code, payload = _run("--check")
        assert code == 0 and payload["credentials"][0]["status"] == "ok"

    def test_required_unreachable_is_exit_3(self, monkeypatch):
        manager.store_credential("example.com", {"username": "u", "password": "p"})
        monkeypatch.setattr(health, "check_wordpress", _checker("unreachable", "timeout"))
        code, _ = _run("--check")
        assert code == 3


class TestProviderFilter:
    def test_unknown_provider_is_exit_2(self):
        code, payload = _run("--provider", "nope")
        assert code == 2 and payload["error"]["code"] == "INVALID_ARGUMENTS"

    def test_named_target_reported_even_if_not_stored(self):
        code, payload = _run("--provider", "wp:site.ru")
        [entry] = payload["credentials"]
        assert entry["provider"] == "wp:site.ru" and entry["status"] == "missing"
        assert code == 3  # названная цель обязательна, а доступа нет

    def test_project_site_uses_project_env(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".env").write_text(
            "SITE_DOMAIN=https://site.local/\nSITE_REST_API_USER=u\nSITE_REST_API_PASSWORD=p\n"
        )
        result = CliRunner().invoke(app, ["-P", str(project), "auth", "status", "--json"])
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        [entry] = payload["credentials"]
        assert entry["provider"] == "wp:site.local"
        assert entry["source"] == "file" and str(project / ".env") in entry["source_ref"]


class TestLogin:
    def test_login_from_env_non_interactive(self, monkeypatch):
        monkeypatch.setenv("WP_API_TOKEN", "pw")
        result = CliRunner().invoke(
            app, ["auth", "login", "--provider", "wp:example.com", "--username", "ed", "--non-interactive", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert manager.stored_names() == ["example.com"]
        assert manager.stored_fields("example.com") == {"username": "ed", "password": "pw"}

    def test_login_without_password_non_interactive_is_exit_2(self):
        result = CliRunner().invoke(
            app, ["auth", "login", "--provider", "wp:example.com", "--username", "ed", "--non-interactive", "--json"]
        )
        assert result.exit_code == 2

    def test_login_rejects_non_wp_provider(self):
        result = CliRunner().invoke(app, ["auth", "login", "--provider", "example.com", "--json"])
        assert result.exit_code == 2


class TestWordPressCheck:
    def _config(self):
        return WordPressConfig(url="https://example.com", username="u", password="p")

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [(200, "ok"), (401, "invalid"), (403, "invalid"), (429, "unreachable"), (502, "unreachable")],
    )
    def test_status_mapping(self, status_code, expected):
        def handler(request):
            assert request.url.path == "/wp-json/wp/v2/users/me"
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(status_code, json={"slug": "u"})

        assert check_credentials(self._config(), transport=httpx.MockTransport(handler)).status == expected

    def test_network_error_is_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        assert check_credentials(self._config(), transport=httpx.MockTransport(handler)).status == "unreachable"
