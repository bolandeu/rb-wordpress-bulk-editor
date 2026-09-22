"""
Tests for src/credentials/manager.py (OS keychain + переменные окружения)
"""

import json

import keyring.errors
import pytest

from src.exceptions import CredentialStoreUnavailable
from src.credentials import manager


class FakeKeyring:
    """Заменитель backend'а keyring — настоящий OS keychain в тестах не трогаем."""

    def __init__(self, fail_on: str = ""):
        self.store = {}
        self.fail_on = fail_on

    def get_password(self, service, user):
        if self.fail_on == "get":
            raise keyring.errors.KeyringError("locked")
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        if self.fail_on == "set":
            raise keyring.errors.KeyringError("locked")
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        if self.fail_on == "delete":
            raise keyring.errors.KeyringError("locked")
        if (service, user) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, user)]


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(manager.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(manager.keyring, "set_password", fake.set_password)
    monkeypatch.setattr(manager.keyring, "delete_password", fake.delete_password)
    return fake


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("WP_API_USER", "WP_API_TOKEN", "WP_EXAMPLE_COM_API_USER", "WP_EXAMPLE_COM_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)


# ── Имена переменных окружения ───────────────────────────────────────

class TestEnvVarNames:
    def test_site_specific_first_then_global(self):
        assert manager.env_var_names("example.com", "password") == [
            "WP_EXAMPLE_COM_API_TOKEN",
            "WP_API_TOKEN",
        ]

    def test_username_field(self):
        assert manager.env_var_names("example.com", "username")[-1] == "WP_API_USER"

    def test_domain_with_scheme_and_port(self):
        assert manager.env_var_names("https://site.example.com:8443", "username")[0] == (
            "WP_HTTPS_SITE_EXAMPLE_COM_8443_API_USER"
        )


# ── Keychain ─────────────────────────────────────────────────────────

class TestKeychain:
    def test_store_and_get_roundtrip(self, fake_keyring):
        manager.store_credential("example.com", {"username": "admin", "password": "s3cret"})
        assert manager.get_credential("example.com") == {"username": "admin", "password": "s3cret"}

    def test_stored_as_json_blob_per_provider(self, fake_keyring):
        manager.store_credential("example.com", {"username": "admin", "password": "s3cret"})
        raw = fake_keyring.store[("wp-bulk:example.com", "example.com")]
        assert json.loads(raw)["username"] == "admin"

    def test_get_missing_returns_none(self, fake_keyring):
        assert manager.get_credential("example.com") is None

    def test_delete_removes_credential(self, fake_keyring):
        manager.store_credential("example.com", {"username": "admin", "password": "x"})
        manager.delete_credential("example.com")
        assert manager.get_credential("example.com") is None

    def test_delete_missing_is_not_an_error(self, fake_keyring):
        manager.delete_credential("example.com")

    def test_unavailable_store_on_write_raises_cli_error(self, monkeypatch):
        fake = FakeKeyring(fail_on="set")
        monkeypatch.setattr(manager.keyring, "set_password", fake.set_password)
        with pytest.raises(CredentialStoreUnavailable) as exc:
            manager.store_credential("example.com", {"password": "x"})
        assert exc.value.code == "CREDENTIAL_STORE_UNAVAILABLE"
        assert exc.value.exit_code == 1

    def test_unavailable_store_on_read_falls_back_to_env(self, monkeypatch):
        fake = FakeKeyring(fail_on="get")
        monkeypatch.setattr(manager.keyring, "get_password", fake.get_password)
        monkeypatch.setenv("WP_API_TOKEN", "from-env")
        assert manager.get_credential("example.com") == {"password": "from-env"}


# ── Переменные окружения ─────────────────────────────────────────────

class TestEnvOverrides:
    def test_env_beats_keychain_field_by_field(self, fake_keyring, monkeypatch):
        manager.store_credential("example.com", {"username": "admin", "password": "old"})
        monkeypatch.setenv("WP_API_TOKEN", "rotated")
        assert manager.get_credential("example.com") == {"username": "admin", "password": "rotated"}

    def test_site_specific_env_beats_global(self, monkeypatch):
        monkeypatch.setenv("WP_API_TOKEN", "global")
        monkeypatch.setenv("WP_EXAMPLE_COM_API_TOKEN", "site")
        assert manager.read_fields_from_env("example.com", ["password"]) == {"password": "site"}

    def test_explicit_environ_snapshot_is_used(self):
        fields = manager.read_fields_from_env(
            "example.com", ["username"], environ={"WP_API_USER": "snapshot"}
        )
        assert fields == {"username": "snapshot"}
