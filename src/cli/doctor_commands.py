"""
Диагностика окружения: doctor.

Собирает набор структурированных проверок (одинаковых для человека и для
`--json`) и не требует живого сайта: сетевой запрос делается только по
явному `--check-connection`.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from dotenv import dotenv_values

from . import app, console
from .helpers import get_project, resolve_env_file
from . import project as project_module
from .project import MIN_VERSION_KEY, PACKAGE_NAME
from .. import output
from ..config import Settings, WordPressConfig
from ..credentials.manager import (
    CREDENTIAL_FIELDS,
    env_var_names,
    get_credential,
    read_fields_from_env,
    store_backend,
)
from ..exceptions import CliTooOld, WPBulkEditorError

# Статусы проверки: ok — всё хорошо, warn — работает, но с оговоркой,
# fail — эта часть окружения сломана.
OK = "ok"
WARN = "warn"
FAIL = "fail"

_STATUS_MARK = {OK: ("green", "OK"), WARN: ("yellow", "WARN"), FAIL: ("red", "FAIL")}


def _check(name: str, status: str, message: str, **details: Any) -> Dict[str, Any]:
    """Один элемент отчёта doctor."""
    item: Dict[str, Any] = {"name": name, "status": status, "message": message}
    if details:
        item["details"] = details
    return item


def _project_check(ctx: typer.Context) -> Dict[str, Any]:
    """Активен ли проект и найден ли его .env."""
    proj = get_project(ctx)
    if not proj.active:
        return _check(
            "project",
            WARN,
            "Проект не выбран; используются глобальный .env и текущая папка.",
            active=False,
        )

    env_file = proj.env_file
    return _check(
        "project",
        OK if env_file else WARN,
        f"Проект '{proj.name}': {proj.root}"
        + ("" if env_file else " (.env не найден)"),
        active=True,
        project=proj.name,
        root=str(proj.root),
        env_file=str(env_file) if env_file else None,
    )


def _cli_compat_check(ctx: typer.Context) -> Dict[str, Any]:
    """Совместимость установленной версии с WP_BULK_MIN_VERSION проекта."""
    proj = get_project(ctx)
    current = project_module.installed_version()
    required = proj.min_version if proj.active else None

    details = {"installed": current, "required": required, "package": PACKAGE_NAME}

    if current is None:
        return _check(
            "cli_compat",
            WARN,
            "Пакет не установлен (запуск из исходников), версия неизвестна — проверка пропущена.",
            **details,
        )
    if not required:
        return _check(
            "cli_compat",
            OK,
            f"Версия {current}; проект минимум не объявляет ({MIN_VERSION_KEY} в .env).",
            **details,
        )

    try:
        project_module.check_cli_compat(proj, version=current)
    except CliTooOld as e:
        return _check("cli_compat", FAIL, e.message, **details)

    return _check(
        "cli_compat", OK, f"Версия {current} >= требуемой {required}.", **details
    )


def _settings_check(env_file: Optional[Path]) -> Dict[str, Any]:
    """Читается ли конфигурация (Settings) без падения и что в ней важного."""
    try:
        settings = Settings(_env_file=env_file) if env_file else Settings()
    except Exception as e:  # noqa: BLE001 — любая ошибка чтения конфига это диагноз
        return _check("config", FAIL, f"Не удалось прочитать настройки: {type(e).__name__}: {e}")

    return _check(
        "config",
        OK,
        f"Настройки читаются; verify_ssl={settings.verify_ssl}.",
        verify_ssl=settings.verify_ssl,
        api_timeout=settings.api_timeout,
        media_timeout=settings.media_timeout,
        max_retries=settings.max_retries,
        sync_concurrency=settings.sync_concurrency,
        taxonomies=settings.get_taxonomies(),
        env_file=str(env_file) if env_file else None,
    )


def _resolve_credentials(
    env_file: Optional[Path],
    domain: Optional[str],
    username: Optional[str],
    password: Optional[str],
) -> Dict[str, Any]:
    """
    Пройти цепочку CLI → окружение → keychain → .env и запомнить, что откуда.

    Окружение процесса читается до значений .env, поэтому источник не
    маскируется: `.env` честно остаётся последним, legacy-слоем.
    """
    process_env = dict(os.environ)
    file_values = dotenv_values(env_file) if env_file else {}

    sources: Dict[str, Optional[str]] = {}

    resolved_domain = domain or process_env.get("WP_DOMAIN") or file_values.get("WP_DOMAIN")
    sources["domain"] = (
        "cli" if domain else
        "env" if process_env.get("WP_DOMAIN") else
        ".env" if file_values.get("WP_DOMAIN") else None
    )
    resolved_domain = (resolved_domain or "").strip() or None

    from_env: Dict[str, str] = {}
    from_keychain: Dict[str, str] = {}
    if resolved_domain:
        from_env = read_fields_from_env(
            resolved_domain, list(CREDENTIAL_FIELDS), environ=process_env
        )
        merged = get_credential(
            resolved_domain, env_fields=list(CREDENTIAL_FIELDS), environ=process_env
        ) or {}
        from_keychain = {k: v for k, v in merged.items() if k not in from_env}

    overrides = {"username": username, "password": password}
    legacy = {"username": "WP_API_USER", "password": "WP_API_TOKEN"}

    values: Dict[str, Optional[str]] = {}
    for field in CREDENTIAL_FIELDS:
        if overrides[field]:
            values[field], sources[field] = overrides[field], "cli"
        elif from_env.get(field):
            values[field], sources[field] = from_env[field], "env"
        elif from_keychain.get(field):
            values[field], sources[field] = from_keychain[field], "keychain"
        elif file_values.get(legacy[field]):
            values[field], sources[field] = file_values[legacy[field]], ".env"
        else:
            values[field], sources[field] = None, None

    return {
        "domain": resolved_domain,
        "username": values["username"],
        "password": values["password"],
        "sources": sources,
    }


def _credentials_check(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Разрешаются ли credentials (не проверяя их валидность на сайте)."""
    sources = resolved["sources"]
    missing = [
        name for name in ("domain", "username", "password") if not resolved.get(name)
    ]

    details = {
        "domain": resolved["domain"],
        "sources": sources,
        # Значения секретов наружу не отдаём — только факт наличия и источник.
        "username": (resolved["username"] or "")[:3] + "***" if resolved["username"] else None,
    }

    if missing:
        hint_var = env_var_names(resolved["domain"] or "", "password")[0]
        return _check(
            "credentials",
            WARN,
            "Не разрешены: " + ", ".join(missing)
            + f" (например, переменная {hint_var}). Read-only командам этого достаточно.",
            missing=missing,
            **details,
        )

    chain = ", ".join(f"{field}: {sources[field]}" for field in ("domain", "username", "password"))
    return _check("credentials", OK, f"Разрешены ({chain}).", **details)


def _credential_store_check() -> Dict[str, Any]:
    """Доступен ли OS keychain для записи секретов."""
    backend = store_backend()
    if backend is None:
        return _check(
            "credential_store",
            WARN,
            "OS keychain недоступен; секреты можно передать только через окружение.",
            backend=None,
        )
    return _check("credential_store", OK, f"OS keychain доступен: {backend}.", backend=backend)


def _connection_check(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Опциональная проверка доступности сайта (только по --check-connection)."""
    domain = resolved["domain"]
    if not domain:
        return _check(
            "connection", WARN, "Домен не разрешён — проверять нечего.", domain=None
        )

    authenticated = bool(resolved["username"] and resolved["password"])
    try:
        config = WordPressConfig.from_credentials(
            domain, resolved["username"] or "", resolved["password"] or ""
        )
        asyncio.run(_probe(config, authenticated))
    except WPBulkEditorError as e:
        return _check("connection", FAIL, e.message, domain=domain, authenticated=authenticated)

    return _check(
        "connection",
        OK,
        f"WordPress REST API отвечает ({domain}"
        + (", авторизовано)." if authenticated else ", анонимно)."),
        domain=domain,
        authenticated=authenticated,
    )


async def _probe(config: WordPressConfig, authenticated: bool) -> None:
    """Лёгкий запрос к WP REST API."""
    from ..wordpress.client import WordPressClient

    async with WordPressClient(config) as client:
        await client.test_connection(authenticated=authenticated)


def _overall(checks: List[Dict[str, Any]]) -> str:
    """Худший статус среди проверок."""
    statuses = {c["status"] for c in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK


@app.command()
@output.guard
def doctor(
    ctx: typer.Context,
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(
        None, "--password", help="WordPress Application Password"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env проекта)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    check_connection: bool = typer.Option(
        False,
        "--check-connection",
        help="Дополнительно проверить, отвечает ли сайт (сетевой запрос).",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Диагностика окружения: проект, версия, конфиг, credentials.

    Проверки: project, cli_compat, config, credentials, credential_store
    (и connection — только с --check-connection).

    Без --check-connection живой сайт не нужен: команда быстрая и офлайн.
    Результат проверок — в payload (`status` каждой проверки и общий `status`),
    сам запуск диагностики успешен.

    Exit-коды: 0 — диагностика выполнена, 1 — непредвиденная ошибка.
    """
    env_file = resolve_env_file(ctx, env_file)

    resolved = _resolve_credentials(env_file, domain, username, password)

    checks = [
        _project_check(ctx),
        _cli_compat_check(ctx),
        _settings_check(env_file),
        _credentials_check(resolved),
        _credential_store_check(),
    ]
    if check_connection:
        checks.append(_connection_check(resolved))

    def _render(payload: Dict[str, Any]):
        console.print("\n[bold blue]wp-bulk doctor[/bold blue]\n")
        for item in payload["checks"]:
            color, mark = _STATUS_MARK[item["status"]]
            console.print(f"[{color}]{mark:<4}[/{color}] {item['name']:<18} {item['message']}")
        color, mark = _STATUS_MARK[payload["status"]]
        console.print(f"\n[bold {color}]Итог: {mark}[/bold {color}]\n")

    output.emit_success(
        ctx,
        {"status": _overall(checks), "checks": checks},
        _render,
    )
