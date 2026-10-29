"""`wp-bulk auth --help` names the whole credential chain; names come from the code, not a copied list."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.credentials import manager
from src.utils import auth

HELP_ENV = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}
DOMAIN = "example.com"


def _help(*args: str) -> str:
    result = CliRunner().invoke(app, [*args, "--help"], env=HELP_ENV)
    assert result.exit_code == 0
    return re.sub(r"\s+", " ", result.output)


@pytest.fixture(scope="module")
def auth_help() -> str:
    return _help("auth")


def test_order_of_sources_is_stated(auth_help: str) -> None:
    positions = [auth_help.index(marker) for marker in ("Флаги", "Окружение", "keychain, запись", ".env проекта")]
    assert positions == sorted(positions)


@pytest.mark.parametrize("field", manager.CREDENTIAL_FIELDS)
def test_env_names_of_manager_are_in_help(auth_help: str, field: str) -> None:
    for name in manager.env_var_names(DOMAIN, field):
        assert name.replace("EXAMPLE_COM", "<SLUG>") in auth_help


def test_domain_slug_rule_is_shown_on_example(auth_help: str) -> None:
    assert f"{DOMAIN} → {manager.env_var_names(DOMAIN, 'username')[0]}" in auth_help


def test_antagor_aliases_are_in_help(auth_help: str) -> None:
    for name in auth._ANTAGOR_ENV_ALIASES.values():
        assert name in auth_help


def test_site_prod_is_declared_unread(auth_help: str) -> None:
    assert "SITE_PROD не читается" in auth_help
    assert "SITE_PROD" not in auth._ANTAGOR_ENV_ALIASES.values()


def test_doppler_is_not_read_and_mapping_is_shown(auth_help: str) -> None:
    assert "не читает Doppler" in auth_help
    assert "doppler run --project projects --config prd -- sh -c" in auth_help
    assert f'{auth._LEGACY_ENV_VARS["password"]}="$TZM_SITE_REST_API_PASSWORD"' in auth_help


def test_status_json_and_anonymous_reads_are_mentioned(auth_help: str) -> None:
    assert "wp-bulk auth status --json" in auth_help
    assert "--check" in auth_help
    assert "анонимно" in auth_help


def test_root_help_points_to_auth_help() -> None:
    assert "wp-bulk auth --help" in _help()
