"""
Команды работы с медиатекой: media-upload (файлы с диска → вложения WordPress).

Команда ничего не знает про файл данных проекта: получает пути (аргументами
или манифестом), грузит, отдаёт id вложений. Связать id с записями
(`featured_media`) — работа вызывающего (antagor).
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import typer
from loguru import logger

from . import app, console
from .. import output
from .common import load_and_print_credentials, setup_command_logging
from .helpers import resolve_env_file
from ..config import WordPressConfig
from ..exceptions import ValidationError, WPBulkEditorError
from ..utils.async_helpers import AsyncBatchProcessor
from ..wordpress.client import WordPressClient
from ..wordpress.media import MediaEndpoint

# Поля элемента манифеста → аргумент MediaEndpoint. `path` обязателен.
_META_FIELDS = {"alt": "alt_text", "title": "title", "caption": "caption"}
_MANIFEST_FIELDS = {"path", *_META_FIELDS}


def _read_manifest(manifest: Path) -> List[Dict[str, Any]]:
    """
    Прочитать манифест: массив объектов `{"path", "alt"?, "title"?, "caption"?}`.

    Проверяется целиком до загрузки: плохой манифест — ошибка аргументов
    (exit 2) со списком всех проблем. Лишний ключ — тоже ошибка: опечатка
    `atl` иначе молча оставила бы картинки без alt.
    """
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValidationError(f"Манифест не найден: {manifest}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValidationError(f"Манифест {manifest.name} не читается как JSON: {e}")

    if not isinstance(data, list):
        raise ValidationError(f"Манифест {manifest.name}: ожидается массив объектов")
    if not data:
        raise ValidationError(f"Манифест {manifest.name} пуст — загружать нечего")

    problems: List[str] = []
    for n, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            problems.append(f"элемент {n}: ожидается объект")
            continue
        unknown = sorted(set(item) - _MANIFEST_FIELDS)
        if unknown:
            problems.append(f"элемент {n}: неизвестные поля {', '.join(unknown)}")
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            problems.append(f"элемент {n}: нет пути (path)")
        for field in _META_FIELDS:
            value = item.get(field)
            if value is not None and not isinstance(value, str):
                problems.append(f"элемент {n}: {field} должен быть строкой")
    if problems:
        raise ValidationError(
            f"Манифест {manifest.name} некорректен:\n  " + "\n  ".join(problems),
            action='Формат: [{"path": "...", "alt": "...", "title": "...", "caption": "..."}]',
        )
    return data


def _build_items(
    paths: Optional[List[str]],
    manifest: Optional[Path],
    alt: Optional[str],
    title: Optional[str],
    caption: Optional[str],
) -> List[Dict[str, Any]]:
    """Единый список элементов из любого режима: дальше путь общий."""
    if manifest and paths:
        raise ValidationError(
            "--manifest и пути аргументами взаимоисключающи",
            action="Передайте либо --manifest, либо пути к файлам.",
        )
    if manifest:
        if alt is not None or title is not None or caption is not None:
            raise ValidationError(
                "--alt/--title/--caption применяются только к путям-аргументам",
                action="В режиме --manifest задайте их полями элементов манифеста.",
            )
        return _read_manifest(manifest)
    if not paths:
        raise ValidationError(
            "Не указано, что загружать",
            action="Передайте пути к файлам аргументами или --manifest <файл.json>.",
        )
    return [{"path": p, "alt": alt, "title": title, "caption": caption} for p in paths]


class _Progress:
    """
    Строка `[17/104] 3425.jpg -> id=4821` по завершении каждого файла —
    тот же формат, что у sync/async (`src/sync/progress.py`): по ней
    вызывающий видит долю сделанного. Номер — сколько сделано, не позиция.
    """

    def __init__(self, total: int):
        self.total = total
        self.done = 0

    def report(self, path: str, outcome: str) -> None:
        self.done += 1
        logger.info(f"[{self.done}/{self.total}] {Path(path).name} -> {outcome}")


def _failure(path: str, error: Exception) -> Dict[str, Any]:
    code = error.code if isinstance(error, WPBulkEditorError) else "UNEXPECTED"
    return {"path": path, "error": str(error), "code": code}


def _all_failed_error(failed: List[Dict[str, Any]], errors: List[Exception]) -> WPBulkEditorError:
    """
    Ни один файл не загрузился → ошибка с кодом одной из причин.

    Ошибка сайта (401, 5xx, сеть) важнее ошибки отдельного файла (нет на
    диске): отсутствующие файлы вызывающий видит сам, а про сайт — нет.
    """
    site_errors = [e for e in errors if isinstance(e, WPBulkEditorError) and e.exit_code != 2]
    cause = site_errors[0] if site_errors else errors[0]
    first = failed[0]
    error = WPBulkEditorError(
        f"Ни один файл не загружен ({len(failed)}). Первая ошибка: {Path(first['path']).name}: {first['error']}",
        action=getattr(cause, "action", None),
    )
    if isinstance(cause, WPBulkEditorError):
        error.code = cause.code
        error.exit_code = cause.exit_code
    return error


async def _upload_all(
    wp_config: WordPressConfig,
    items: List[Dict[str, Any]],
    concurrency: int,
    progress: _Progress,
) -> List[Dict[str, Any]]:
    """Загрузить файлы параллельно; результат — по элементу на файл, в порядке входа."""
    processor = AsyncBatchProcessor(concurrency=concurrency)

    async with WordPressClient(wp_config) as client:
        media = MediaEndpoint(client)

        async def upload_one(item: Dict[str, Any]) -> Dict[str, Any]:
            path = item["path"]
            try:
                # Файл и метаданные — раздельно: если упадёт второй запрос,
                # вложение на сайте уже есть, и его id нужно вернуть, а не
                # объявить файл незагруженным (повтор создал бы дубль).
                result = await media.upload_file(Path(path))
            except Exception as e:
                progress.report(path, f"error: {e}")
                raise
            entry = {"path": path, "id": result["id"], "source_url": result.get("source_url")}
            meta = {arg: item.get(field) for field, arg in _META_FIELDS.items() if item.get(field)}
            if meta:
                try:
                    await media.update(result["id"], **meta)
                except Exception as e:
                    logger.warning(
                        f"{Path(path).name}: вложение id={result['id']} создано, "
                        f"но alt/title/caption не проставлены: {e}"
                    )
                    entry["meta_error"] = str(e)
            progress.report(path, f"id={result['id']}")
            return entry

        tasks = [processor.process_row("upload", n, item, upload_one, item) for n, item in enumerate(items)]
        results = await asyncio.gather(*tasks)

    return [
        {"ok": True, "entry": r.api_response}
        if r.success
        else {
            "ok": False,
            "entry": _failure(items[cast(int, r.row_index)]["path"], cast(Exception, r.error)),
            "error": r.error,
        }
        for r in results
    ]


@app.command("media-upload")
@output.guard
def media_upload(
    ctx: typer.Context,
    paths: Optional[List[str]] = typer.Argument(
        None,
        help="Пути к файлам (абсолютные или от текущей папки). Взаимоисключающи с --manifest",
        show_default=False,
    ),
    manifest: Optional[Path] = typer.Option(
        None,
        "--manifest",
        help='JSON-массив [{"path", "alt"?, "title"?, "caption"?}] — пакетная загрузка',
        dir_okay=False,
    ),
    alt: Optional[str] = typer.Option(None, "--alt", help="Alt-текст для всех путей-аргументов"),
    title: Optional[str] = typer.Option(None, "--title", help="Заголовок вложения для всех путей-аргументов"),
    caption: Optional[str] = typer.Option(None, "--caption", help="Подпись для всех путей-аргументов"),
    concurrency: int = typer.Option(5, "--concurrency", min=1, help="Количество параллельных загрузок"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Проверить манифест и наличие файлов, к сайту не обращаться (id: null)",
    ),
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", help="WordPress Application Password"),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Загрузка файлов с диска в медиатеку WordPress -> id вложений.

    Пути — аргументами (--alt/--title/--caption применяются ко всем) или
    манифестом --manifest. Файлы данных проекта команда не читает и не пишет:
    связать id с записями (featured_media) — дело вызывающего. Дубли не
    ищутся — грузится ровно то, что передано; alt/title/caption ставятся
    только присланные.

    Ответ: uploaded [{path, id, source_url, meta_error?}] и failed
    [{path, error, code}]; path — дословно как передан. meta_error —
    вложение создано, но alt/title/caption не проставились.
    Частичный успех — успех (exit 0). По ходу — строки \\[n/total] в лог.

    Exit-коды: 0 — загружен хотя бы один файл, 2 — аргументы/манифест или
    ни одного файла нет на диске, 3 — нет credentials, 5 — недоступен WordPress.

    Example: {cli} media-upload media/1.jpg media/2.jpg --alt "Фото" --json
    """
    items = _build_items(paths, manifest, alt, title, caption)

    log_file = setup_command_logging(ctx, "media-upload")
    progress = _Progress(len(items))

    # Отсутствующие файлы отсекаются до сайта: при опечатке в путях не нужны
    # ни доступы, ни запросы.
    outcomes: List[Optional[Dict[str, Any]]] = [None] * len(items)
    to_upload: List[int] = []
    for n, item in enumerate(items):
        path = item["path"]
        if Path(path).is_file():
            to_upload.append(n)
            continue
        error = ValidationError(f"Файл не найден: {path}")
        progress.report(path, f"error: {error}")
        outcomes[n] = {"ok": False, "entry": _failure(path, error), "error": error}

    if dry_run:
        for n in to_upload:
            progress.report(items[n]["path"], "id=- (dry-run)")
            outcomes[n] = {"ok": True, "entry": {"path": items[n]["path"], "id": None, "source_url": None}}
    elif to_upload:
        domain, username, password = load_and_print_credentials(
            env_file=resolve_env_file(ctx, env_file),
            domain=domain,
            username=username,
            password=password,
            ctx=ctx,
        )
        wp_config = WordPressConfig.from_credentials(domain, username, password)
        uploaded = asyncio.run(_upload_all(wp_config, [items[n] for n in to_upload], concurrency, progress))
        for n, outcome in zip(to_upload, uploaded):
            outcomes[n] = outcome

    # К этому месту заполнены все outcomes (ни одного None) — отсюда ty: ignore.
    uploaded_entries = [o["entry"] for o in outcomes if o["ok"]]  # ty: ignore[not-subscriptable]
    failed_entries = [o["entry"] for o in outcomes if not o["ok"]]  # ty: ignore[not-subscriptable]
    if not uploaded_entries:
        raise _all_failed_error(failed_entries, [o["error"] for o in outcomes if not o["ok"]])  # ty: ignore[not-subscriptable]

    payload = {
        "uploaded": uploaded_entries,
        "failed": failed_entries,
        "uploaded_count": len(uploaded_entries),
        "failed_count": len(failed_entries),
        "dry_run": dry_run,
        "log_file": str(log_file),
    }

    def _render(data: dict) -> None:
        verb = "Проверено (dry-run)" if dry_run else "Загружено"
        console.print(f"\n[bold green]{verb}: {data['uploaded_count']}[/bold green]")
        for entry in data["uploaded"]:
            note = (
                f"  [yellow](alt/title не проставлены: {entry['meta_error']})[/yellow]"
                if entry.get("meta_error")
                else ""
            )
            ident = f"id={entry['id']}" if entry["id"] is not None else "файл на месте"
            console.print(f"  {entry['path']} -> {ident}{note}")
        if data["failed"]:
            console.print(f"[bold red]Ошибки: {data['failed_count']}[/bold red]")
            for entry in data["failed"]:
                console.print(f"  {entry['path']}: {entry['error']}")

    output.emit_success(ctx, payload, _render)
