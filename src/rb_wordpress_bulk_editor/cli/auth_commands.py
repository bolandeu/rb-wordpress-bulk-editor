"""
Группа `wp-bulk auth`: здоровье доступов (стандарт §8) и хранение доступов сайтов в keychain.
"""

from typing import Optional

import typer

from . import app
from .. import output
from ..credentials import health, manager
from ..exceptions import ValidationError
from ..utils.auth import _ANTAGOR_ENV_ALIASES
from ..version import installed_version
from .helpers import get_project

_EXAMPLE_DOMAIN = "example.com"


def _help_names(field: str) -> str:
    """Имена переменных поля для справки: из кода, не списком от руки."""
    return ", ".join(manager.env_var_names(_EXAMPLE_DOMAIN, field)).replace("EXAMPLE_COM", "<SLUG>")


_AUTH_HELP = f"""Доступы: отчёт об их здоровье и хранение доступов сайтов в OS keychain.

Откуда wp-bulk берёт домен, логин и пароль сайта. Первый источник, где поле есть, \
выигрывает — для каждого поля отдельно:

1. Флаги команды: --domain / --username / --password (где они есть).

2. Окружение процесса. Домен: WP_DOMAIN. Логин: {_help_names("username")}. \
Пароль приложения: {_help_names("password")}. Сначала имя сайта, затем общее. \
<SLUG> — домен заглавными, всё кроме букв и цифр заменено на «_» \
({_EXAMPLE_DOMAIN} → {manager.env_var_names(_EXAMPLE_DOMAIN, "username")[0]}). \
Запасные имена antagor: {_ANTAGOR_ENV_ALIASES["domain"]}, {_ANTAGOR_ENV_ALIASES["username"]}, \
{_ANTAGOR_ENV_ALIASES["password"]}; свои WP_* главнее, а при заданном SITE_CMS не равном \
wordpress алиасы отключены. SITE_PROD не читается никогда.

3. OS keychain, запись wp:<домен>: `wp-bulk auth login --provider wp:<домен>`.

4. .env проекта (legacy, последним): ищется от текущей папки вверх или берётся из --project. \
Имена те же (WP_DOMAIN, WP_API_USER, WP_API_TOKEN и алиасы antagor).

Секрет-менеджер. wp-bulk сам не читает никакое хранилище секретов (Vault, 1Password, \
Windmill — любое). Хранилище кладёт значения в окружение под именами выше — это делает \
вызывающий. Имена там могут нести префикс сайта (TZM_SITE_REST_API_PASSWORD): wp-bulk \
префикс не отрезает, переименовывает вызывающий:

\b
  env WP_DOMAIN=tzm.example.com \
      WP_API_USER="$TZM_SITE_REST_API_USER" \
      WP_API_TOKEN="$TZM_SITE_REST_API_PASSWORD" \
      wp-bulk fetch ...

Что используется на деле: `wp-bulk auth status --json` — источник каждого поля \
(cli/env/keychain/.env), без значений; --check проверяет доступ одним read-only запросом.

Доступов нет — не ошибка для команд только на чтение (работают анонимно); \
для записи нужны.
"""

auth_app = output.group(_AUTH_HELP)
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
        help="Только этот провайдер: wp или wp:<домен> (по умолчанию — все).",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """Отчёт о каждом доступе: откуда он берётся и работает ли. Значений не печатает.

    Сайты WordPress — по записи `wp:<домен>` на каждую цель: сохранённые в
    keychain (`auth login`), сайт активного проекта (та же цепочка, что у sync:
    окружение → keychain → .env проекта) и цель из --provider.

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
