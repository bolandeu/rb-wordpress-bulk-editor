"""
Tests for src/output/ — JSON-конверт и диспетчеризация exit-кодов в guard.
"""

import json

import click
import pytest
import typer

from src import output
from src.output import json as output_json
from src.version import installed_version
from src.exceptions import (
    Aborted,
    AuthenticationError,
    CredentialStoreUnavailable,
    ServerError,
    ValidationError,
)
from src.utils.retry import CircuitBreakerOpenError


def make_ctx(json_mode: bool = False, verbose: bool = False) -> typer.Context:
    ctx = typer.Context(click.Command("test"))
    options = output.get_options(ctx)
    options.json = json_mode
    options.verbose = verbose
    return ctx


def read_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip())


# ── Конверт ──────────────────────────────────────────────────────────

class TestEnvelope:
    def test_success_envelope(self, capsys):
        output.emit_success(make_ctx(json_mode=True), {"file": "a.xlsx", "count": 2})
        assert read_json(capsys) == {
            "ok": True, "file": "a.xlsx", "count": 2, "cli_version": installed_version(),
        }

    def test_cli_version_in_every_envelope(self, capsys, monkeypatch):
        """antagor пишет в журнал, чем сделан прогон — в том числе упавший."""
        monkeypatch.setattr(output_json, "installed_version", lambda: "9.9.9")

        output.emit_success(make_ctx(json_mode=True), {"file": "a.json"})
        assert read_json(capsys)["cli_version"] == "9.9.9"

        with pytest.raises(typer.Exit):
            output.emit_error(make_ctx(json_mode=True), ValidationError("x"))
        assert read_json(capsys)["cli_version"] == "9.9.9"

    def test_error_envelope(self, capsys):
        with pytest.raises(typer.Exit):
            output.emit_error(make_ctx(json_mode=True), ValidationError("плохой аргумент"))

        payload = read_json(capsys)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert payload["error"]["message"] == "плохой аргумент"

    def test_provider_error_carries_provider_and_status(self, capsys):
        error = ServerError("упал", provider="wordpress", status=503)
        with pytest.raises(typer.Exit):
            output.emit_error(make_ctx(json_mode=True), error)

        payload = read_json(capsys)["error"]
        assert payload["code"] == "PROVIDER_ERROR"
        assert payload["provider"] == "wordpress"
        assert payload["status"] == 503
        assert payload["action"]

    def test_human_mode_keeps_stdout_clean(self, capsys):
        with pytest.raises(typer.Exit):
            output.emit_error(make_ctx(json_mode=False), ValidationError("ошибка"))

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "ошибка" in captured.err

    def test_human_success_uses_renderer(self, capsys):
        output.emit_success(make_ctx(), {"count": 3}, lambda p: print(f"готово: {p['count']}"))
        assert capsys.readouterr().out.strip() == "готово: 3"

    def test_quiet_suppresses_human_output(self, capsys):
        ctx = make_ctx()
        output.get_options(ctx).quiet = True
        output.emit_success(ctx, {"count": 3}, lambda p: print("шумно"))
        assert capsys.readouterr().out == ""


# ── guard: exit-коды ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "error, expected_code, expected_exit",
    [
        (ValidationError("плохой аргумент"), "INVALID_ARGUMENTS", 2),
        (AuthenticationError("нет доступа"), "AUTH_REQUIRED", 3),
        (ServerError("500"), "PROVIDER_ERROR", 5),
        (CircuitBreakerOpenError("открыт"), "PROVIDER_ERROR", 5),
        (CredentialStoreUnavailable("keychain закрыт"), "CREDENTIAL_STORE_UNAVAILABLE", 1),
    ],
)
def test_guard_dispatches_exit_codes(capsys, error, expected_code, expected_exit):
    @output.guard
    def command(ctx: typer.Context):
        raise error

    with pytest.raises(typer.Exit) as exc:
        command(ctx=make_ctx(json_mode=True))

    assert exc.value.exit_code == expected_exit
    assert read_json(capsys)["error"]["code"] == expected_code


def test_guard_wraps_unmapped_exception_as_unexpected(capsys):
    @output.guard
    def command(ctx: typer.Context):
        raise RuntimeError("внезапно")

    with pytest.raises(typer.Exit) as exc:
        command(ctx=make_ctx(json_mode=True))

    assert exc.value.exit_code == 1
    error = read_json(capsys)["error"]
    assert error["code"] == "UNEXPECTED"
    assert "RuntimeError" in error["message"]
    assert "--verbose" in error["action"]


def test_guard_passes_through_typer_exit():
    @output.guard
    def command(ctx: typer.Context):
        raise typer.Exit(code=0)

    with pytest.raises(typer.Exit) as exc:
        command(ctx=make_ctx())

    assert exc.value.exit_code == 0


# ── Гейт подтверждения ───────────────────────────────────────────────

class TestRequireConfirmation:
    def test_confirm_flag_skips_the_question(self, monkeypatch):
        monkeypatch.setattr(typer, "confirm", lambda *a, **kw: pytest.fail("спросил, хотя был --confirm"))
        output.require_confirmation(make_ctx(), "удалить всё?", confirm=True)

    def test_non_interactive_without_confirm_is_invalid_arguments(self):
        ctx = make_ctx()
        output.get_options(ctx).non_interactive = True

        with pytest.raises(ValidationError) as exc:
            output.require_confirmation(ctx, "удалить всё?")

        assert exc.value.exit_code == 2
        assert "--confirm" in exc.value.action

    def test_json_mode_never_prompts(self):
        with pytest.raises(ValidationError):
            output.require_confirmation(make_ctx(json_mode=True), "удалить всё?")

    def test_user_refusal_is_aborted(self, monkeypatch):
        monkeypatch.setattr(output, "is_interactive", lambda ctx: True)
        monkeypatch.setattr(typer, "confirm", lambda *a, **kw: False)

        with pytest.raises(Aborted) as exc:
            output.require_confirmation(make_ctx(), "удалить всё?")

        assert exc.value.exit_code == 1
        assert exc.value.code == "ABORTED"


def test_guard_picks_up_local_json_flag(capsys):
    @output.guard
    def command(ctx: typer.Context, json_output: bool = False):
        raise ValidationError("ошибка")

    ctx = make_ctx(json_mode=False)
    with pytest.raises(typer.Exit):
        command(ctx=ctx, json_output=True)

    assert read_json(capsys)["ok"] is False
