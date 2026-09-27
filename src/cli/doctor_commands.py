"""
Диагностика окружения: doctor.

Собирает набор структурированных проверок (одинаковых для человека и для
`--json`) и не требует живого сайта: сетевой запрос делается только по
явному `--check-connection`.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from . import app, console
from .helpers import get_project, resolve_env_file
from . import project as project_module
from .project import MIN_VERSION_KEY, PACKAGE_NAME
from .. import output
from ..config import Settings, WordPressConfig
from ..credentials.manager import env_var_names, store_backend
from ..exceptions import CliTooOld, WPBulkEditorError
from ..utils.auth import ResolvedCredentials, resolve_credentials

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


def _credentials_check(resolved: ResolvedCredentials) -> Dict[str, Any]:
    """Разрешаются ли credentials (не проверяя их валидность на сайте)."""
    sources = resolved.sources
    missing = resolved.missing

    details = {
        "domain": resolved.domain or None,
        "sources": sources,
        # Значения секретов наружу не отдаём — только факт наличия и источник.
        "username": resolved.username[:3] + "***" if resolved.username else None,
    }

    if missing:
        hint_var = env_var_names(resolved.domain, "password")[0]
        return _check(
            "credentials",
            WARN,
            "Не разрешены: " + ", ".join(missing)
            + f" (например, переменная {hint_var}). Read-only командам этого достаточно.",
            missing=missing,
            **details,
        )

    chain = ", ".join(f"{name}: {sources[name]}" for name in ("domain", "username", "password"))
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


def _connection_check(resolved: ResolvedCredentials) -> Dict[str, Any]:
    """Опциональная проверка доступности сайта (только по --check-connection)."""
    domain = resolved.domain
    if not domain:
        return _check(
            "connection", WARN, "Домен не разрешён — проверять нечего.", domain=None
        )

    authenticated = bool(resolved.username and resolved.password)
    try:
        config = WordPressConfig.from_credentials(
            domain, resolved.username, resolved.password
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

    Example: {cli} doctor --check-connection --json
    """
    env_file = resolve_env_file(ctx, env_file)

    # Та же функция, что использует sync: doctor обязан видеть ровно то же,
    # что увидит реальная команда (включая .env в текущей папке).
    resolved = resolve_credentials(
        env_file=env_file,
        domain_override=domain,
        username_override=username,
        password_override=password,
    )

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
