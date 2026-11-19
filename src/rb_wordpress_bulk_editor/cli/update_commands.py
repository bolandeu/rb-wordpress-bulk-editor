"""
Команда update: подтянуть отдельные поля из WordPress в файл данных по id.

Файл данных — Excel или JSON (по расширению `--file`), WordPress только читается.
"""

import asyncio
from pathlib import Path
from typing import List, Optional, Set

import typer

from . import app, console
from .. import output
from .common import load_and_print_credentials, parse_comma_list, setup_command_logging
from .helpers import require_data_file, resolve_env_file
from ..config import Settings, WordPressConfig
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..exceptions import ValidationError
from ..records.excel import ExcelRecordSource, ExcelRecordWriter
from ..records.json_source import JsonRecordSource, JsonRecordWriter
from ..records.selection import dataset_names, is_json_source, require_json_tabs
from ..sync.field_pull import PullReport, PullTarget, pull_fields
from ..wordpress.client import WordPressClient


def _parse_ids(ids: Optional[str]) -> Optional[Set[int]]:
    """`--id 1,2,3` → {1, 2, 3}; None — все записи с id."""
    if ids is None:
        return None
    values = parse_comma_list(ids) or []
    try:
        parsed = {int(value) for value in values}
    except ValueError:
        raise ValidationError(f"--id должен быть списком чисел через запятую: {ids!r}") from None
    if not parsed:
        raise ValidationError("--id не содержит ни одного id")
    return parsed


@app.command("update")
@output.guard
def update_from_wp(
    ctx: typer.Context,
    ids: Optional[str] = typer.Option(
        None,
        "--id",
        help="ID записей через запятую: 123,456,789. Без --id — все записи с id.",
    ),
    columns: str = typer.Option(
        ...,
        "--columns",
        "-c",
        help="Поля для обновления из WordPress: title,excerpt,seo.title,acf.f1",
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Файл данных .xlsx или .json (авто-определяется из --project)",
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Листы Excel / табы JSON через запятую. Для JSON обязателен",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Показать изменения без записи в файл"),
    test_mode: bool = typer.Option(False, "--test-mode", help="Только первые 3 записи каждого набора"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу"),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Подтянуть указанные поля из WordPress в файл данных по ID.

    Источник выбирается по расширению --file: .xlsx — листы Excel, .json —
    табы JSON (для JSON --sheets обязателен: рядом с контентом лежат служебные
    табы). Без --id обновляются все записи с заполненным id. Контент и термины
    таксономий в одном табе различаются полями type/taxonomy.

    WordPress не изменяется — только чтение. Dot-нотация в --columns
    (seo.title, acf.f1) работает как обычное имя поля. В Excel пишутся только
    существующие колонки, в JSON недостающие поля записи добавляются.
    --dry-run не открывает файл на запись.

    Exit-коды: 0 — успех, 2 — аргументы/структура файла, 3 — нет credentials,
    5 — недоступен WordPress, 1 — непредвиденная ошибка.

    Example: {cli} update --file site.json --sheets site.ru --columns slug,link --dry-run
    """
    env_file = resolve_env_file(ctx, env_file)
    file = require_data_file(ctx, file)
    id_filter = _parse_ids(ids)
    column_list = parse_comma_list(columns) or []
    if not column_list:
        raise ValidationError("--columns не может быть пустым — укажите хотя бы одно поле")

    # Выбор наборов и структура файла проверяются до credentials:
    # ошибка в файле не должна требовать доступа к сайту.
    if is_json_source(file):
        names = require_json_tabs(file, sheets)
        for tab in names:
            JsonRecordSource(file, tab).read()
    else:
        names = dataset_names(sheets) if sheets is not None else None

    setup_command_logging(ctx, "update", verbose)
    domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)
    wp_config = WordPressConfig.from_credentials(domain, username, password)

    report = asyncio.run(
        _run_update(
            wp_config,
            file,
            names,
            column_list,
            id_filter,
            dry_run=dry_run,
            test_mode=test_mode,
        )
    )

    def _render(payload: dict):
        prefix = "DRY-RUN " if payload["dry_run"] else ""
        for item in payload["items"]:
            console.print(f"[green]✓[/green] {prefix}{item['dataset']} id={item['id']}: {', '.join(item['fields'])}")
        for warning in payload["warnings"]:
            console.print(f"[yellow]WARN[/yellow] {warning}")
        verb = "Будет обновлено" if payload["dry_run"] else "Обновлено"
        console.print(f"\n[bold]{verb}: {payload['updated']} из {payload['found']} найденных записей[/bold]")

    output.emit_success(
        ctx,
        {"file": str(file), "dry_run": dry_run, "columns": column_list, **report.to_dict()},
        _render,
    )


async def _run_update(
    wp_config: WordPressConfig,
    file: Path,
    names: Optional[List[str]],
    columns: List[str],
    id_filter: Optional[Set[int]],
    dry_run: bool,
    test_mode: bool,
) -> PullReport:
    """Открыть источник, подтянуть поля, сохранить файл (writer'ы — только вне dry-run)."""
    report = PullReport()
    async with WordPressClient(wp_config) as client:
        await client.test_connection()
        if is_json_source(file):
            await _update_json(client, file, names or [], columns, id_filter, dry_run, test_mode, report)
        else:
            await _update_excel(client, file, names, columns, id_filter, dry_run, test_mode, report)
    return report


async def _update_json(client, file, tabs, columns, id_filter, dry_run, test_mode, report) -> None:
    """Таб за табом, у каждого свой writer (файл перечитывается перед табом)."""
    for tab in tabs:
        records = list(JsonRecordSource(file, tab).read())
        writer = None if dry_run else JsonRecordWriter(file, tab)
        if writer:
            writer.load()
        try:
            await pull_fields(
                client,
                [PullTarget(tab, records, writer)],
                columns,
                id_filter=id_filter,
                test_mode=test_mode,
                report=report,
            )
        finally:
            # Сохраняем и на пути ошибки: подтянутое до сбоя не должно пропасть.
            if writer:
                writer.save()


async def _update_excel(client, file, sheets, columns, id_filter, dry_run, test_mode, report) -> None:
    """Контентные и таксономические листы; служебные (options, media) не трогаются."""
    with ExcelReader(file, known_taxonomies=Settings().get_taxonomies()) as reader:
        by_type = reader.get_sheets_by_type()
        available = by_type["content"] + by_type["taxonomy"]
        if sheets is None:
            sheets = available
        unknown = [name for name in sheets if name not in available]
        if unknown:
            raise ValidationError(
                f"Листы не найдены среди контентных/таксономий: {', '.join(unknown)}. "
                f"Доступные: {', '.join(available) or 'нет'}"
            )
        sources = {name: ExcelRecordSource(reader, name) for name in sheets}
        loaded = {name: (list(src.read()), set(src.fields())) for name, src in sources.items()}

    excel_writer = None if dry_run else ExcelWriter(file)
    if excel_writer:
        excel_writer.load()
    try:
        targets = [
            PullTarget(
                name,
                records,
                ExcelRecordWriter(excel_writer, name) if excel_writer else None,
                known_fields=fields,
            )
            for name, (records, fields) in loaded.items()
        ]
        await pull_fields(client, targets, columns, id_filter=id_filter, test_mode=test_mode, report=report)
    finally:
        if excel_writer:
            excel_writer.save()
