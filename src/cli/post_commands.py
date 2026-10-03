"""
WP-адаптер для Windmill (T104): `post upsert`, `post get`, `media attach`.

Одна запись за вызов, вход — JSON (`--input <файл|->`), выход — JSON-конверт.
Сайт — `--site`, доступы — только окружение `WP_API_USER` / `WP_API_TOKEN`
(`utils/auth.adapter_credentials`): без keychain, `.env` и флагов, поэтому
команды одинаково работают на Mac и на сервере (rb-run). Файлов логов не
пишут — лог идёт в stderr. Логика — `src/wordpress/post_adapter.py`.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

import typer

from . import app, console
from .. import output
from ..config import WordPressConfig
from ..exceptions import AuthenticationError, AuthFailed, ValidationError
from ..utils.auth import ADAPTER_ENV_VARS, adapter_credentials
from ..wordpress import post_adapter
from ..wordpress.client import WordPressClient

T = TypeVar("T")

post_app = output.group("Одна запись WordPress за вызов (адаптер Windmill): черновик/обновление, чтение.")
app.add_typer(post_app, name="post")
media_app = output.group("Медиа записи: изображение (featured) для существующей записи.")
app.add_typer(media_app, name="media")

_ENV_NAMES = " / ".join(ADAPTER_ENV_VARS)

SiteOption = typer.Option(None, "--site", help="Домен сайта (example.com или https://example.com)")
TypeOption = typer.Option("post", "--type", help="Тип записи WordPress (post, page, rb_services…)")
# Доступы флагами не принимаются: пароль в argv виден в списке процессов и истории.
PasswordOption = typer.Option(None, "--password", hidden=True, help=f"Не поддерживается: пароль только из {_ENV_NAMES}")
UsernameOption = typer.Option(None, "--username", hidden=True, help=f"Не поддерживается: логин только из {_ENV_NAMES}")


def _refuse_flag_credentials(username: Optional[str], password: Optional[str]) -> None:
    """Доступы флагами — отказ (exit 2) раньше любых других проверок."""
    if password is not None or username is not None:
        raise ValidationError(
            "Доступы флагами не принимаются: пароль в командной строке виден в списке процессов",
            action=f"Задайте переменные окружения {_ENV_NAMES}.",
        )


def _connect(site: Optional[str]) -> WordPressConfig:
    """Сайт и доступы из окружения, до сети: нет env — AUTH_MISSING."""
    if not site or not site.strip():
        raise ValidationError("Не указан сайт", action="Передайте --site <домен>.")
    user, token = adapter_credentials()
    return WordPressConfig.from_credentials(site.strip(), user, token)


def _run(config: WordPressConfig, operation: Callable[[WordPressClient], Awaitable[T]]) -> T:
    """Выполнить операцию на сайте; отказ в доступе (401/403) — AUTH_FAILED."""

    async def _go() -> T:
        async with WordPressClient(config) as client:
            return await operation(client)

    try:
        return asyncio.run(_go())
    except AuthFailed:
        raise
    except AuthenticationError as e:
        raise AuthFailed(e.message, provider=e.provider, status=e.status)


def _read_input(source: Optional[str]) -> str:
    if source is None:
        raise ValidationError("Не указан вход", action="Передайте --input <файл.json> или --input - (stdin).")
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValidationError(f"Файл входа не найден: {source}")
    except (OSError, UnicodeDecodeError) as e:
        raise ValidationError(f"Файл входа не читается: {source}: {e}")


def _render_fields(data: Dict[str, Any]) -> None:
    for key, value in data.items():
        console.print(f"  {key}: {value}")


@post_app.command("upsert")
@output.guard
def post_upsert(
    ctx: typer.Context,
    input_path: Optional[str] = typer.Option(
        None,
        "--input",
        help="JSON {type?, id?, slug, title, content, seo?, featured?}: путь к файлу или - (stdin)",
    ),
    post_id: Optional[int] = typer.Option(None, "--id", min=1, help="ID записи для обновления (иначе — поиск по slug)"),
    site: Optional[str] = SiteOption,
    username: Optional[str] = UsernameOption,
    password: Optional[str] = PasswordOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Создать черновик или обновить запись WordPress (одна запись за вызов).

    Вход — JSON-объект {type?, id?, slug, title, content, seo?, featured?}:
    type — тип записи (по умолчанию post); content — готовый HTML/Gutenberg,
    без конвертации; seo — объект поля rb-seo ({title, description, …});
    featured — изображение записи {path|url|id, alt?}.

    С id (во входе или --id) — обновление этой записи, статус не меняется;
    slug не-черновика тоже: живой URL сохраняется, slug из входа отброшен
    (slug_kept: true в ответе). У черновика slug обновляется.
    Без id — поиск по тип+slug: черновик обновляется, не-черновик — CONFLICT
    (ничего не меняется), нет записи — создаётся черновик (только draft).
    У типа нет поля seo (rb-seo выключен) — SEO_NOT_SUPPORTED до записи.

    Доступы — только окружение WP_API_USER / WP_API_TOKEN.
    Ответ: {id, url, status, modified, content_hash, created, slug, slug_kept,
    featured_media?}; content_hash — sha256 от content.raw, который сохранил
    WordPress; slug — текущий slug записи на сайте.

    Exit-коды: 0 — готово; 1 — NOT_FOUND (id, медиа или файл изображения),
    CONFLICT, SEO_NOT_SUPPORTED; 2 — неверный вход или флаги; 3 — AUTH_MISSING,
    AUTH_FAILED; 5 — недоступен WordPress.

    Example: {cli} post upsert --site example.com --input post.json --json
    """
    # Порядок: флаги → вход → файл изображения → доступы → сеть.
    _refuse_flag_credentials(username, password)
    spec = post_adapter.parse_upsert_input(_read_input(input_path), post_id)
    if spec.featured is not None:
        spec.featured.check_file()
    config = _connect(site)

    result = _run(config, lambda client: post_adapter.upsert(client, spec))

    def _render(data: Dict[str, Any]) -> None:
        verb = "Создан черновик" if data["created"] else "Обновлено"
        console.print(f"[bold green]{verb}: id={data['id']}[/bold green]")
        _render_fields({k: v for k, v in data.items() if k not in ("id", "created")})

    output.emit_success(ctx, result, _render)


@post_app.command("get")
@output.guard
def post_get(
    ctx: typer.Context,
    post_id: Optional[int] = typer.Option(None, "--id", min=1, help="ID записи"),
    post_type: str = TypeOption,
    site: Optional[str] = SiteOption,
    username: Optional[str] = UsernameOption,
    password: Optional[str] = PasswordOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Прочитать запись WordPress по ID (context=edit, сырые поля).

    Ответ: {id, type, slug, title, content, status, url, modified, seo,
    featured_media, content_hash}; title и content — raw, как в редакторе;
    seo — null, если у типа нет поля rb-seo; content_hash — тот же, что
    вернул post upsert для этого контента.

    Доступы — только окружение WP_API_USER / WP_API_TOKEN.

    Exit-коды: 0 — готово; 1 — NOT_FOUND; 2 — неверные флаги или тип;
    3 — AUTH_MISSING, AUTH_FAILED; 5 — недоступен WordPress.

    Example: {cli} post get --id 42 --site example.com --json
    """
    _refuse_flag_credentials(username, password)
    if post_id is None:
        raise ValidationError("Не указан id записи", action="Передайте --id <N>.")
    config = _connect(site)

    result = _run(config, lambda client: post_adapter.get_post(client, post_type, post_id))

    def _render(data: Dict[str, Any]) -> None:
        console.print(f"[bold]{data['title']}[/bold] (id={data['id']}, {data['status']})")
        _render_fields({k: v for k, v in data.items() if k not in ("id", "title", "content", "status")})

    output.emit_success(ctx, result, _render)


@media_app.command("attach")
@output.guard
def media_attach(
    ctx: typer.Context,
    post_id: Optional[int] = typer.Option(None, "--post", min=1, help="ID записи, которой ставится изображение"),
    path: Optional[str] = typer.Option(None, "--path", help="Локальный файл: загрузить и привязать"),
    url: Optional[str] = typer.Option(None, "--url", help="URL файла: загрузить на сайт и привязать"),
    media_id: Optional[int] = typer.Option(
        None, "--media-id", min=1, help="ID уже загруженного медиа: привязать без загрузки"
    ),
    alt: Optional[str] = typer.Option(None, "--alt", help="Alt-текст медиа"),
    post_type: str = TypeOption,
    site: Optional[str] = SiteOption,
    username: Optional[str] = UsernameOption,
    password: Optional[str] = PasswordOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Сделать медиа изображением (featured) существующей записи.

    Источник — ровно один из --path, --url, --media-id. Запись проверяется до
    загрузки файла; статус записи не меняется. Доступы — только окружение
    WP_API_USER / WP_API_TOKEN.

    Ответ: {media_id, source_url, post_id}.

    Exit-коды: 0 — готово; 1 — NOT_FOUND (запись, медиа или файл); 2 — неверные
    флаги; 3 — AUTH_MISSING, AUTH_FAILED; 5 — недоступен WordPress.

    Example: {cli} media attach --post 42 --path cover.jpg --alt "Обложка" --site example.com --json
    """
    _refuse_flag_credentials(username, password)
    if post_id is None:
        raise ValidationError("Не указан id записи", action="Передайте --post <N>.")
    featured = post_adapter.FeaturedSource.build(path, url, media_id, alt, where="media attach")
    featured.check_file()
    config = _connect(site)

    result = _run(config, lambda client: post_adapter.attach_featured(client, post_type, post_id, featured))

    def _render(data: Dict[str, Any]) -> None:
        console.print(f"[bold green]Запись {data['post_id']}: изображение id={data['media_id']}[/bold green]")
        console.print(f"  {data['source_url']}")

    output.emit_success(ctx, result, _render)
