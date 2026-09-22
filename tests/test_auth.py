"""
Tests for src/utils/auth.py — порядок разрешения credentials.

CLI-флаг → переменная окружения → OS keychain → legacy .env.
"""

import json
import os

import pytest

from src.exceptions import AuthenticationError, ConfigurationError
from src.credentials import manager
from src.utils.auth import load_credentials_from_env


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Свой снимок окружения: load_dotenv пишет в os.environ, не пачкаем соседей."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for name in ("WP_DOMAIN", "WP_API_USER", "WP_API_TOKEN"):
        os.environ.pop(name, None)


@pytest.fixture
def keychain(monkeypatch):
    """Фейковый keychain: {provider: {поля}}."""
    store = {}

    def get_password(service, user):
        return json.dumps(store[user]) if user in store else None

    monkeypatch.setattr(manager.keyring, "get_password", get_password)
    return store


def write_env(tmp_path, **values):
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return env_file


class TestResolutionOrder:
    def test_cli_flags_win(self, tmp_path, keychain, monkeypatch):
        keychain["example.com"] = {"username": "keychain", "password": "keychain-pass"}
        monkeypatch.setenv("WP_API_USER", "env-user")
        env_file = write_env(tmp_path, WP_DOMAIN="https://dotenv.example", WP_API_USER="dotenv")

        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override="https://cli.example",
            username_override="cli-user",
            password_override="cli-pass",
        )

        assert (domain, username, password) == ("https://cli.example", "cli-user", "cli-pass")

    def test_env_beats_keychain(self, tmp_path, keychain, monkeypatch):
        keychain["example.com"] = {"username": "keychain", "password": "keychain-pass"}
        monkeypatch.setenv("WP_DOMAIN", "example.com")
        monkeypatch.setenv("WP_API_TOKEN", "env-pass")
        env_file = write_env(tmp_path)

        _, username, password = load_credentials_from_env(env_file=env_file)

        assert username == "keychain"  # из keychain, его никто не перекрыл
        assert password == "env-pass"

    def test_keychain_used_without_env_or_dotenv(self, tmp_path, keychain, monkeypatch):
        keychain["example.com"] = {"username": "keychain", "password": "keychain-pass"}
        monkeypatch.setenv("WP_DOMAIN", "example.com")
        env_file = write_env(tmp_path)

        domain, username, password = load_credentials_from_env(env_file=env_file)

        assert (domain, username, password) == ("example.com", "keychain", "keychain-pass")

    def test_dotenv_is_last_resort(self, tmp_path, keychain):
        env_file = write_env(
            tmp_path,
            WP_DOMAIN="https://dotenv.example",
            WP_API_USER="dotenv-user",
            WP_API_TOKEN="dotenv-pass",
        )

        domain, username, password = load_credentials_from_env(env_file=env_file)

        assert (domain, username, password) == (
            "https://dotenv.example",
            "dotenv-user",
            "dotenv-pass",
        )

    def test_env_beats_dotenv(self, tmp_path, keychain, monkeypatch):
        monkeypatch.setenv("WP_API_TOKEN", "env-pass")
        env_file = write_env(
            tmp_path,
            WP_DOMAIN="https://dotenv.example",
            WP_API_USER="dotenv-user",
            WP_API_TOKEN="dotenv-pass",
        )

        _, _, password = load_credentials_from_env(env_file=env_file)

        assert password == "env-pass"


class TestFailures:
    def test_missing_credentials_raise_auth_required(self, tmp_path, keychain):
        env_file = write_env(tmp_path)

        with pytest.raises(AuthenticationError) as exc:
            load_credentials_from_env(env_file=env_file)

        assert exc.value.code == "AUTH_REQUIRED"
        assert exc.value.exit_code == 3

    def test_missing_env_file_is_configuration_error(self, tmp_path, keychain):
        with pytest.raises(ConfigurationError) as exc:
            load_credentials_from_env(env_file=tmp_path / "нет.env")

        assert exc.value.exit_code == 2
