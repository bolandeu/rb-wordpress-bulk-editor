"""
Группа `wp-bulk auth`: здоровье доступов (стандарт §8) и хранение доступов сайтов в keychain.
"""

from typing import Optional

import typer

from . import app
from .. import output
from ..credentials import health, manager
from ..exceptions import ValidationError
from ..version import installed_version
from .helpers import get_project

auth_app = output.group("Доступы: отчёт об их здоровье и хранение доступов сайтов в OS keychain.")
app.add_typer(auth_app, name="auth")

_WP_PREFIX = f"{health.WP_KIND}:"


def _domain(provider: str) -> str:
    """`wp:<домен>` → домен; иначе ошибка аргументов (exit 2)."""
    domain = provider.removeprefix(_WP_PREFIX).strip() if provider.startswith(_WP_PREFIX) else ""
    if not domain:
        raise ValidationError(
            f"Ожидается --provider {_WP_PREFIX}<домен>, получено '{provider}'.",
            action=f"Пример: wp-bulk auth login --provider {_WP_PREFIX}example.com",
        )
    return domain


@auth_app.command("status")
@output.guard
def auth_status(
    ctx: typer.Context,
    check: bool = typer.Option(
        False, "--check", help="Проверить каждый доступ одним дешёвым read-only запросом (предел 10 с)."
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="Только этот провайдер: wp, wp:<домен>, llmkit или llmkit:<провайдер> (по умолчанию — все).",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """Отчёт о каждом доступе: откуда он берётся и работает ли. Значений не печатает.

    Сайты WordPress — по записи `wp:<домен>` на каждую цель: сохранённые в
    keychain (`auth login`), сайт активного проекта (та же цепочка, что у sync:
    окружение → keychain → .env проекта) и цель из --provider. Ключи LLM —
    записи `llmkit:<провайдер>` из отчёта llmkit, необязательные.

    Без --check — только наличие (`unchecked` / `missing`). С --check у сайта
    `GET /wp-json/wp/v2/users/me`: 401/403 — `invalid`, сеть/таймаут/5xx/429 —
    `unreachable`. Exit 0 — все обязательные ok/unchecked; 3 — у обязательного
    проблема или любой доступ invalid/expired (конверт ok:false +
    CREDENTIALS_UNHEALTHY и весь отчёт); 2 — неизвестный --provider.
    Форма --json — общий контракт: `tool`, `version`, `checked_at`,
    `credentials[]` c `provider`, `required`, `fields`, `source`, `source_ref`,
    `status`, `expires_at`, `quota`, `message`.

    Example: {cli} auth status --check --json
    """
    project = get_project(ctx)
    payload, problem = health.status(
        "wp-bulk",
        installed_version() or "0.0.0-dev",
        check=check,
        only=provider,
        project_env_file=project.env_file,
        project_active=project.active,
    )
    output.emit_report(ctx, payload, lambda data: health.render(data, output.human.echo), problem)


@auth_app.command("login")
@output.guard
def auth_login(
    ctx: typer.Context,
    provider: str = typer.Option(..., "--provider", help="Сайт: wp:<домен>, например wp:example.com."),
    username: Optional[str] = typer.Option(
        None, "--username", help="Логин WordPress (иначе WP_<DOMAIN>_API_USER / WP_API_USER или вопрос)."
    ),
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """Сохранить логин и пароль приложения сайта в OS keychain.

    Пароль берётся из WP_<DOMAIN>_API_TOKEN / WP_API_TOKEN, иначе спрашивается
    скрытым вводом; флага для пароля нет, чтобы он не попал в историю shell.
    Без терминала (или с --non-interactive) недостающее поле — ошибка, exit 2.
    Сохранённый сайт появляется в `auth status`.

    Example: {cli} auth login --provider wp:example.com --username editor
    """
    domain = _domain(provider)
    fields = manager.read_fields_from_env(domain)
    if username:
        fields["username"] = username.strip()
    for field, label in (("username", "Логин WordPress"), ("password", "Пароль приложения")):
        if fields.get(field):
            continue
        if not output.is_interactive(ctx):
            names = " / ".join(manager.env_var_names(domain, field))
            raise ValidationError(
                f"Не задано поле '{field}' для {provider}, а спрашивать нельзя.",
                action=f"Передайте его через {names}" + (" или --username." if field == "username" else "."),
            )
        fields[field] = typer.prompt(f"{label} для {domain}", hide_input=field == "password").strip()
    manager.store_credential(domain, {k: fields[k] for k in manager.CREDENTIAL_FIELDS})
    output.emit_success(
        ctx,
        {"provider": f"{_WP_PREFIX}{domain}", "stored": True},
        lambda data: output.human.echo(f"Доступ сохранён в keychain: {data['provider']}"),
    )


@auth_app.command("logout")
@output.guard
def auth_logout(
    ctx: typer.Context,
    provider: str = typer.Option(..., "--provider", help="Сайт: wp:<домен>."),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """Удалить доступ сайта из OS keychain. Отсутствующий доступ — не ошибка.

    Example: {cli} auth logout --provider wp:example.com
    """
    domain = _domain(provider)
    manager.delete_credential(domain)
    output.emit_success(
        ctx,
        {"provider": f"{_WP_PREFIX}{domain}", "removed": True},
        lambda data: output.human.echo(f"Доступ удалён из keychain: {data['provider']}"),
    )
