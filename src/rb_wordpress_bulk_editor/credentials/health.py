"""
Отчёт `wp-bulk auth status [--check] --json` (tools/docs/standard-release.md §8).

Форма — общий контракт инструментов: `tools-watch` читает её, не зная
провайдеров, поэтому её фиксирует `tests/test_auth_status.py`, а изменение
формы — ломающее изменение. Значения credentials отсюда не выходят: они
нужны только чтобы вычистить их из сообщений.

Что попадает в отчёт:
- `wp:<домен>` — по записи на каждую цель: домены, сохранённые в keychain
  (`auth login`), домен активного проекта (та же цепочка, что у `sync`:
  окружение → keychain → `.env` проекта) и цель, названная в `--provider`.
  Сохранённая цель обязательна (`required: true`): раз её сохранили, ею
  пользуются. Целей нет — одна запись `wp`, `missing`, необязательная:
  чтение публичного контента работает и анонимно.
"""

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, cast

from rich.markup import escape
from rb_credentials.status import entry as _record
from rb_credentials.status import problems, scrub
from rb_credentials.status import report as _envelope

from ..exceptions import CredentialsUnhealthy, ValidationError, WPBulkEditorError
from . import manager

WP_KIND = "wp"
# Кто побеждает, когда поля пришли из разных мест (как в цепочке разрешения).
_SOURCE_RANK = {"env": 0, "keychain": 1, "file": 2}

Checker = Callable[[str, str, str], Tuple[str, Optional[str]]]


def _blank(provider: str, required: bool, fields: List[str], source_ref: str) -> Dict[str, Any]:
    return cast(Dict[str, Any], _record(provider, required=required, fields=fields, source_ref=source_ref))


# --- WordPress ---------------------------------------------------------------


def target_name(domain: str) -> str:
    """Домен в имени цели: без схемы и слэшей (`https://site.ru/` → `site.ru`)."""
    return domain.strip().split("://", 1)[-1].strip("/").lower()


def _locate_stored(domain: str, environ: Mapping[str, str]) -> Dict[str, Tuple[str, str, str]]:
    """field → (value, source, ref) по окружению и keychain — как `get_credential`."""
    found: Dict[str, Tuple[str, str, str]] = {}
    stored = manager.stored_fields(domain)
    for field in manager.CREDENTIAL_FIELDS:
        for name in manager.env_var_names(domain, field):
            value = (environ.get(name) or "").strip()
            if value:
                found[field] = (value, "env", name)
                break
        else:
            if stored.get(field):
                found[field] = (stored[field], "keychain", f"keychain service {manager.keychain_service(domain)}")
    return found


def _locate_project(
    env_file: Optional[Path], environ: Mapping[str, str]
) -> Tuple[str, Dict[str, Tuple[str, str, str]]]:
    """(домен, поля) активного проекта — ровно та цепочка, что увидит `sync`."""
    from ..utils.auth import _LEGACY_ENV_VARS, _env_names, resolve_credentials

    resolved = resolve_credentials(env_file=env_file)
    found: Dict[str, Tuple[str, str, str]] = {}
    if not resolved.domain:
        return "", found
    kinds = {"env": "env", "keychain": "keychain", ".env": "file"}
    for field in manager.CREDENTIAL_FIELDS:
        value, source = getattr(resolved, field), resolved.sources.get(field)
        if not value or source not in kinds:
            continue
        if source == "env":
            names = manager.env_var_names(resolved.domain, field)
            ref = next((n for n in names if (environ.get(n) or "").strip()), names[0])
        elif source == "keychain":
            ref = f"keychain service {manager.keychain_service(resolved.domain)}"
        else:
            names = list(_env_names(field, _LEGACY_ENV_VARS[field], dict(os.environ)))
            ref = f"{'/'.join(names)} in {env_file}" if env_file else "/".join(names) + " in .env"
        found[field] = (value, kinds[source], ref)
    return resolved.domain, found


def _wp_source_ref(domain: str) -> str:
    env = ", ".join(manager.env_var_names(domain, f)[0] for f in manager.CREDENTIAL_FIELDS)
    return f"{env} or keychain service {manager.keychain_service(domain)}"


def _wp_entry(
    domain: str,
    located: Dict[str, Tuple[str, str, str]],
    check: bool,
    checker: Checker,
) -> Dict[str, Any]:
    fields = list(manager.CREDENTIAL_FIELDS)
    entry = _blank(f"{WP_KIND}:{target_name(domain)}", True, fields, _wp_source_ref(domain))
    missing = [f for f in fields if f not in located]
    if missing:
        entry["message"] = f"не задано: {', '.join(missing)}"
        return entry
    sources = sorted({located[f][1] for f in fields}, key=_SOURCE_RANK.__getitem__)
    refs = list(dict.fromkeys(located[f][2] for f in fields))
    entry.update(source=sources[0], source_ref="; ".join(refs))
    if not check:
        entry["status"] = "unchecked"
        return entry
    username, password = located["username"][0], located["password"][0]
    status, message = checker(domain, username, password)
    entry.update(status=status, message=scrub(message, [username, password]))
    return entry


def check_wordpress(domain: str, username: str, password: str) -> Tuple[str, Optional[str]]:
    """`auth status --check` для сайта: один запрос через провайдера WordPress."""
    from ..config import WordPressConfig
    from ..providers.wordpress import check_credentials

    try:
        config = WordPressConfig.from_credentials(domain, username, password)
    except ValueError as e:
        return "invalid", f"домен не годится как адрес сайта: {e}"
    health = check_credentials(config)
    return health.status, health.message


def wordpress_entries(
    *,
    check: bool,
    only: Optional[str],
    project_env_file: Optional[Path],
    project_active: bool,
) -> List[Dict[str, Any]]:
    environ = dict(os.environ)  # снимок ДО загрузки .env проекта: .env не выдаётся за окружение
    prefix = f"{WP_KIND}:"
    targets: Dict[str, Dict[str, Tuple[str, str, str]]] = {}
    if only is not None and only.startswith(prefix):
        wanted = only.removeprefix(prefix)
        targets[wanted] = _locate_stored(wanted, environ)
    else:
        for domain in manager.stored_names():
            targets[domain] = _locate_stored(domain, environ)
    if project_active:
        domain, located = _locate_project(project_env_file, environ)
        # Сайт проекта отчитывается тем, что увидит sync (с .env проекта).
        if domain and (only is None or only in (WP_KIND, f"{prefix}{target_name(domain)}")):
            # Тот же сайт, сохранённый в keychain под коротким именем, второй раз не выводим.
            for stored in [d for d in targets if target_name(d) == target_name(domain)]:
                del targets[stored]
            targets[domain] = located
    if not targets:
        entry = _blank(WP_KIND, False, list(manager.CREDENTIAL_FIELDS), _wp_source_ref("<домен>"))
        entry["message"] = f"сайтов нет: ни в keychain ('wp-bulk auth login --provider {prefix}<домен>'), ни в проекте"
        return [entry]
    return [
        _wp_entry(domain, located, check, check_wordpress)
        for domain, located in sorted(targets.items(), key=lambda item: target_name(item[0]))
    ]


# --- отчёт -------------------------------------------------------------------


def _kind(only: Optional[str]) -> Optional[str]:
    if only is None:
        return None
    if only == WP_KIND or only.startswith(f"{WP_KIND}:"):
        return WP_KIND
    raise ValidationError(f"Неизвестный провайдер '{only}' (известны: {WP_KIND}, {WP_KIND}:<домен>).")


def status(
    tool: str,
    version: str,
    *,
    check: bool,
    only: Optional[str],
    project_env_file: Optional[Path],
    project_active: bool,
) -> Tuple[Dict[str, Any], Optional[WPBulkEditorError]]:
    """Что печатает `auth status`: отчёт и проблема (exit 3), если она есть."""
    kind = _kind(only)
    credentials: List[Dict[str, Any]] = []
    if kind in (None, WP_KIND):
        credentials += wordpress_entries(
            check=check, only=only, project_env_file=project_env_file, project_active=project_active
        )
    payload = _envelope(tool, version, credentials)
    unhealthy = problems(payload)
    return payload, CredentialsUnhealthy(unhealthy) if unhealthy else None


def render(payload: Dict[str, Any], echo: Callable[[str], None]) -> None:
    """Человеку: строка на запись."""
    if not payload["credentials"]:
        echo("Провайдеров с доступами нет.")
    for entry in payload["credentials"]:
        mark = {"ok": "[green]✓[/green]", "unchecked": "[dim]·[/dim]"}.get(entry["status"], "[red]✗[/red]")
        optional = "" if entry["required"] else " (необязательный)"
        detail = f" — {entry['message']}" if entry["message"] else ""
        line = f"{entry['provider']}{optional}: {entry['status']} ({entry['source']}: {entry['source_ref']}){detail}"
        echo(f"{mark} {escape(line)}")
