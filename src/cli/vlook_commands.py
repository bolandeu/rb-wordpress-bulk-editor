"""
Команда vlook: привязка файлов к строкам Excel по ID (VLOOKUP по файловой системе).
"""

from pathlib import Path
from typing import Optional, Set, Dict, List, Tuple, cast

import typer
from loguru import logger

from . import app, console
from .helpers import get_project, require_excel_file
from .common import handle_command_errors, setup_command_logging
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter

# Расширения изображений
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".tiff",
    ".tif",
    ".ico",
}

VALID_MODES = {"content", "media", "both", "custom"}


def _resolve_vlook_excel(ctx: typer.Context, explicit: Optional[Path]) -> Path:
    """Resolve Excel через общий helper."""
    return require_excel_file(ctx, explicit)


def _build_file_index(folder: Path, extensions: Optional[Set[str]] = None) -> Dict[str, str]:
    """Индекс {stem: filename} для файлов в папке."""
    index: Dict[str, str] = {}
    if not folder.exists():
        return index
    for item in folder.iterdir():
        if not item.is_file():
            continue
        if extensions and item.suffix.lower() not in extensions:
            continue
        if item.stem not in index:
            index[item.stem] = item.name
    return index


@app.command()
def vlook(
    ctx: typer.Context,
    mode: str = typer.Option(
        "content",
        "--mode",
        "-m",
        help="Режим: content, media, both, custom",
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Excel файл (по умолчанию export.xlsx)",
    ),
    sheet: Optional[str] = typer.Option(
        None,
        "--sheet",
        "-s",
        help="Конкретный лист (по умолчанию все контентные)",
    ),
    folder: Optional[Path] = typer.Option(
        None,
        "--folder",
        help="Папка для поиска (режим custom)",
        resolve_path=True,
    ),
    column: Optional[str] = typer.Option(
        None,
        "--column",
        help="Колонка для записи (режим custom)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи",
    ),
):
    """
    Привязка файлов к строкам Excel по ID (VLOOKUP по файловой системе).

    Ищет файлы {id}.ext в папке и записывает имя файла в колонку Excel.

    \b
    Режимы:
      content - {id}.html в content/ -> content_path (по умолчанию)
      media   - {id}.<картинка> в media/ -> img_path
      both    - content + media одновременно
      custom  - --folder + --column, любые файлы

    Example: wp-bulk vlook --mode media
    """
    if mode not in VALID_MODES:
        console.print(f"[red]ERROR: Неизвестный режим '{mode}'. Допустимые: {', '.join(sorted(VALID_MODES))}[/red]")
        raise typer.Exit(code=1)

    excel_file = _resolve_vlook_excel(ctx, file)
    proj = get_project(ctx)

    with handle_command_errors(ctx):
        setup_command_logging(ctx, "vlook", verbose)

        console.print("\n[bold cyan]VLOOK[/bold cyan] — привязка файлов по ID")
        console.print(f"Файл: [white]{excel_file.name}[/white]")
        console.print(f"Режим: [white]{mode}[/white]\n")

        # --- Валидация custom ---
        if mode == "custom":
            if not folder:
                console.print("[red]ERROR: --folder обязателен для режима custom[/red]")
                raise typer.Exit(code=1)
            if not column:
                console.print("[red]ERROR: --column обязателен для режима custom[/red]")
                raise typer.Exit(code=1)

        # --- Список задач: (папка, колонка, расширения) ---
        tasks: List[Tuple[Path, str, Optional[Set[str]]]] = []

        if mode in ("content", "both"):
            tasks.append((proj.content_folder, "content_path", {".html"}))

        if mode in ("media", "both"):
            tasks.append((proj.media_folder, "img_path", IMAGE_EXTENSIONS))

        if mode == "custom":
            tasks.append((cast(Path, folder), cast(str, column), None))

        # Проверка папок
        for task_folder, task_col, _ in tasks:
            if not task_folder.exists():
                console.print(f"[yellow]WARN: Папка не найдена: {task_folder}[/yellow]")

        # --- Построение индексов файлов ---
        file_indexes: Dict[str, Dict[str, str]] = {}
        for task_folder, _, task_ext in tasks:
            key = str(task_folder)
            if key not in file_indexes:
                idx = _build_file_index(task_folder, task_ext)
                file_indexes[key] = idx
                logger.info(f"Индекс '{task_folder}': {len(idx)} файлов")
                console.print(f"Папка [white]{task_folder}[/white]: [cyan]{len(idx)}[/cyan] файлов")

        # --- Фаза 1: чтение Excel, поиск совпадений ---
        updates: Dict[str, List[Tuple[int, str, str]]] = {}
        total_rows = 0
        total_found = 0

        with ExcelReader(excel_file) as reader:
            if sheet:
                target_sheets = [sheet]
            else:
                sheets_by_type = reader.get_sheets_by_type()
                target_sheets = sheets_by_type.get("content", [])

            if not target_sheets:
                console.print("[yellow]Нет листов для обработки[/yellow]")
                return

            console.print(f"Листы: {', '.join(target_sheets)}\n")

            for sheet_name in target_sheets:
                df = reader.read_sheet(sheet_name)

                if "id" not in df.columns:
                    console.print(f"  [yellow]{sheet_name}[/yellow]: нет колонки 'id', пропуск")
                    continue

                sheet_updates: List[Tuple[int, str, str]] = []

                for task_folder, task_column, task_ext in tasks:
                    if task_column not in df.columns:
                        console.print(f"  [yellow]{sheet_name}[/yellow]: нет колонки '{task_column}', пропуск")
                        continue

                    index = file_indexes.get(str(task_folder), {})
                    if not index:
                        continue

                    sheet_found = 0
                    sheet_total = 0

                    for row_idx, row in df.iterrows():
                        row_id = str(row["id"]).strip()
                        if not row_id:
                            continue
                        sheet_total += 1

                        filename = index.get(row_id)
                        if filename:
                            sheet_updates.append((row_idx, task_column, filename))
                            sheet_found += 1
                            logger.debug(f"[{sheet_name}] id={row_id} → {task_column}={filename}")

                    total_rows += sheet_total
                    total_found += sheet_found

                    logger.info(
                        f"[{sheet_name}] {task_column}: "
                        f"найдено {sheet_found}/{sheet_total} "
                        f"(файлов в папке: {len(index)})"
                    )
                    console.print(
                        f"  [green]{sheet_name}[/green] -> {task_column}: [cyan]{sheet_found}[/cyan]/{sheet_total}"
                    )

                if sheet_updates:
                    updates[sheet_name] = sheet_updates

        # --- Фаза 2: запись в Excel ---
        if updates:
            with ExcelWriter(excel_file) as writer:
                for sheet_name, sheet_updates in updates.items():
                    for row_idx, col, val in sheet_updates:
                        writer.update_row(sheet_name, row_idx, {col: val})

            console.print(
                f"\n[bold green]Готово![/bold green] Обновлено: [cyan]{total_found}[/cyan] из {total_rows} строк"
            )
        else:
            console.print(f"\n[yellow]Совпадений не найдено[/yellow] (проверено {total_rows} строк)")
