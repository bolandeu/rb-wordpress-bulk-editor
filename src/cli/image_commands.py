"""
Команды обработки изображений: image-crop, image-compress, image-convert
"""

from pathlib import Path
from typing import Optional

from loguru import logger
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from . import app, console
from .helpers import get_project, resolve_logs_folder
from ..config import LoggingConfig
from ..utils.logger import setup_logger
from ..utils.image_processor import ImageProcessor, BatchProcessingStats

import typer


# ─── Helpers ───────────────────────────────────────────────────────────────


def _resolve_input_folder(ctx: typer.Context, explicit: Optional[Path]) -> Path:
    """Разрешить input: явный путь > project media/ > ошибка."""
    if explicit:
        return explicit
    proj = get_project(ctx)
    if proj.active:
        return proj.media_folder
    console.print("[red]ERROR: Укажите --input или используйте --project[/red]")
    raise typer.Exit(1)


def _resolve_output_folder(
    input_folder: Path, explicit: Optional[Path], default_subfolder: str
) -> Path:
    """Разрешить output: явный путь > input/subfolder."""
    if explicit:
        return explicit
    return input_folder / default_subfolder


def _print_stats(stats: BatchProcessingStats, log_file: Optional[Path] = None):
    """Вывести результаты обработки."""
    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Label", style="bold")
    table.add_column("Value", style="cyan")

    table.add_row("Всего файлов", str(stats.total))
    table.add_row("Обработано", str(stats.processed))
    if stats.skipped:
        table.add_row("Пропущено", str(stats.skipped))
    if stats.errors:
        table.add_row("Ошибки", f"[red]{stats.errors}[/red]")

    if stats.total_input_bytes > 0:
        table.add_row(
            "Размер входных",
            f"{stats.total_input_bytes / 1024 / 1024:.1f} MB",
        )
        table.add_row(
            "Размер выходных",
            f"{stats.total_output_bytes / 1024 / 1024:.1f} MB",
        )
        if stats.saved_bytes > 0:
            table.add_row(
                "Сэкономлено",
                f"{stats.saved_bytes / 1024 / 1024:.1f} MB ({stats.compression_ratio:.1f}%)",
            )

    console.print(table)
    if log_file:
        console.print(f"\n[dim]Лог: {log_file}[/dim]")
    console.print()


def _setup_logging(ctx: typer.Context, command_name: str, verbose: bool) -> Path:
    """Настройка логирования для image-команд."""
    logs_folder = resolve_logs_folder(ctx)
    logs_folder.mkdir(parents=True, exist_ok=True)
    logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")
    return setup_logger(
        logs_folder=logs_folder,
        config=logging_config,
        command_name=command_name,
    )


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    )


# ─── Commands ──────────────────────────────────────────────────────────────


@app.command()
def image_crop(
    ctx: typer.Context,
    input_folder: Optional[Path] = typer.Option(
        None, "--input", "-i", help="Папка с изображениями"
    ),
    output_folder: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Папка для результатов"
    ),
    width: Optional[int] = typer.Option(
        None, "--width", "-W", help="Ширина в пикселях"
    ),
    height: Optional[int] = typer.Option(
        None, "--height", "-H", help="Высота в пикселях"
    ),
    aspect_ratio: Optional[str] = typer.Option(
        None, "--aspect-ratio", "-a", help="Соотношение сторон (16:9, 4:3, 1:1)"
    ),
    strategy: str = typer.Option(
        "center", "--strategy", "-s", help="Стратегия обрезки: center, top, bottom, left, right"
    ),
    glob_pattern: str = typer.Option(
        "*.{jpg,jpeg,png,webp}", "--glob", "-g", help="Паттерн файлов"
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Искать в подпапках"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Перезаписать существующие"
    ),
    # Gradient overlay
    gradient: float = typer.Option(
        0.0, "--gradient", help="Затемняющий градиент снизу (opacity 0.0-1.0)"
    ),
    gradient_height: float = typer.Option(
        0.5, "--gradient-height", help="Высота градиента (доля 0.0-1.0)"
    ),
    # Watermark
    watermark_text: Optional[str] = typer.Option(
        None, "--watermark-text", "--wt", help="Текст водяного знака"
    ),
    watermark_image: Optional[Path] = typer.Option(
        None, "--watermark-image", "--wi", help="Картинка водяного знака"
    ),
    watermark_position: str = typer.Option(
        "bottom-right", "--watermark-position", "--wp",
        help="Позиция: center, bottom-right, bottom-left, top-right, top-left, bottom-center",
    ),
    watermark_opacity: float = typer.Option(
        0.3, "--watermark-opacity", "--wo", help="Прозрачность водяного знака (0.0-1.0)"
    ),
    watermark_size: Optional[int] = typer.Option(
        None, "--watermark-size", "--ws", help="Размер шрифта (авто если не указан)"
    ),
    watermark_color: str = typer.Option(
        "#FFFFFF", "--watermark-color", "--wc", help="Цвет текста (#RRGGBB)"
    ),
    watermark_scale: float = typer.Option(
        0.2, "--watermark-scale", help="Масштаб картинки-водяного знака (доля от ширины)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Показать что будет сделано"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный вывод"),
):
    """
    Обрезка изображений под размер или соотношение сторон.

    Поддерживает градиентный overlay и водяной знак (текст или картинка).

    Примеры:

        wp-bulk image-crop -P ost --aspect-ratio 16:9

        wp-bulk image-crop --input ./photos --width 800 --height 600

        wp-bulk image-crop -P ost -a 1:1 --strategy top

        wp-bulk image-crop -P ost -a 16:9 --gradient 0.5

        wp-bulk image-crop -P ost -a 16:9 --wt "© MySite" --wp center
    """
    if not aspect_ratio and not (width and height):
        console.print(
            "[red]ERROR: Укажите --aspect-ratio или (--width и --height)[/red]"
        )
        raise typer.Exit(1)

    resolved_input = _resolve_input_folder(ctx, input_folder)
    resolved_output = _resolve_output_folder(resolved_input, output_folder, "cropped")

    if not resolved_input.exists():
        console.print(f"[red]ERROR: Папка не найдена: {resolved_input}[/red]")
        raise typer.Exit(1)

    log_file = _setup_logging(ctx, "image_crop", verbose)

    console.print(f"\n[bold blue]Image Crop[/bold blue]")
    console.print("=" * 60)
    console.print(f"  Input:    [cyan]{resolved_input}[/cyan]")
    console.print(f"  Output:   [cyan]{resolved_output}[/cyan]")
    if aspect_ratio:
        console.print(f"  Aspect:   [cyan]{aspect_ratio}[/cyan]")
    if width and height:
        console.print(f"  Size:     [cyan]{width}x{height}[/cyan]")
    console.print(f"  Strategy: [cyan]{strategy}[/cyan]")
    if gradient > 0:
        console.print(f"  Gradient: [cyan]{gradient} (height {gradient_height})[/cyan]")
    if watermark_text:
        console.print(f"  WM text:  [cyan]{watermark_text} ({watermark_position})[/cyan]")
    if watermark_image:
        console.print(f"  WM image: [cyan]{watermark_image} ({watermark_position})[/cyan]")
    console.print()

    processor = ImageProcessor()
    files = processor.collect_files(resolved_input, glob_pattern, recursive)

    if not files:
        console.print("[yellow]Изображения не найдены[/yellow]")
        return

    console.print(f"Найдено [cyan]{len(files)}[/cyan] изображений\n")

    if dry_run:
        for f in files[:20]:
            console.print(f"  [dim]{f.name}[/dim]")
        if len(files) > 20:
            console.print(f"  ... и ещё {len(files) - 20}")
        console.print("\n[yellow]DRY-RUN: изменения не внесены[/yellow]")
        return

    stats = BatchProcessingStats(total=len(files))

    with _make_progress() as progress:
        task = progress.add_task("Обрезка...", total=len(files))

        for file_path in files:
            output_path = resolved_output / file_path.relative_to(resolved_input)

            if output_path.exists() and not overwrite:
                stats.skipped += 1
                progress.advance(task)
                continue

            result = processor.crop_image(
                source=file_path,
                output=output_path,
                width=width,
                height=height,
                aspect_ratio=aspect_ratio,
                strategy=strategy,
                gradient_overlay=gradient,
                gradient_height=gradient_height,
                watermark_text=watermark_text,
                watermark_image=watermark_image,
                watermark_position=watermark_position,
                watermark_opacity=watermark_opacity,
                watermark_size=watermark_size,
                watermark_color=watermark_color,
                watermark_scale=watermark_scale,
            )
            stats.results.append(result)

            if result.success:
                stats.processed += 1
                stats.total_input_bytes += result.original_size
                stats.total_output_bytes += result.output_size
            else:
                stats.errors += 1

            progress.advance(task)

    _print_stats(stats, log_file)


@app.command()
def image_compress(
    ctx: typer.Context,
    input_folder: Optional[Path] = typer.Option(
        None, "--input", "-i", help="Папка с изображениями"
    ),
    output_folder: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Папка для результатов"
    ),
    quality: int = typer.Option(
        80, "--quality", "-q", help="Качество JPEG/WebP (1-100)", min=1, max=100
    ),
    max_width: Optional[int] = typer.Option(
        None, "--max-width", help="Максимальная ширина (пропорционально)"
    ),
    max_height: Optional[int] = typer.Option(
        None, "--max-height", help="Максимальная высота (пропорционально)"
    ),
    strip_metadata: bool = typer.Option(
        True, "--strip-metadata/--keep-metadata", help="Удалить EXIF метаданные"
    ),
    glob_pattern: str = typer.Option(
        "*.{jpg,jpeg,png,webp}", "--glob", "-g", help="Паттерн файлов"
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Искать в подпапках"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Перезаписать существующие"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Показать что будет сделано"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный вывод"),
):
    """
    Сжатие изображений для веба.

    Примеры:

        wp-bulk image-compress -P ost --quality 80

        wp-bulk image-compress --input ./photos -q 75 --max-width 1920
    """
    resolved_input = _resolve_input_folder(ctx, input_folder)
    resolved_output = _resolve_output_folder(
        resolved_input, output_folder, "compressed"
    )

    if not resolved_input.exists():
        console.print(f"[red]ERROR: Папка не найдена: {resolved_input}[/red]")
        raise typer.Exit(1)

    log_file = _setup_logging(ctx, "image_compress", verbose)

    console.print(f"\n[bold blue]Image Compress[/bold blue]")
    console.print("=" * 60)
    console.print(f"  Input:    [cyan]{resolved_input}[/cyan]")
    console.print(f"  Output:   [cyan]{resolved_output}[/cyan]")
    console.print(f"  Quality:  [cyan]{quality}[/cyan]")
    if max_width:
        console.print(f"  Max W:    [cyan]{max_width}px[/cyan]")
    if max_height:
        console.print(f"  Max H:    [cyan]{max_height}px[/cyan]")
    console.print(
        f"  Metadata: [cyan]{'strip' if strip_metadata else 'keep'}[/cyan]"
    )
    console.print()

    processor = ImageProcessor()
    files = processor.collect_files(resolved_input, glob_pattern, recursive)

    if not files:
        console.print("[yellow]Изображения не найдены[/yellow]")
        return

    console.print(f"Найдено [cyan]{len(files)}[/cyan] изображений\n")

    if dry_run:
        for f in files[:20]:
            console.print(f"  [dim]{f.name}[/dim]")
        if len(files) > 20:
            console.print(f"  ... и ещё {len(files) - 20}")
        console.print("\n[yellow]DRY-RUN: изменения не внесены[/yellow]")
        return

    stats = BatchProcessingStats(total=len(files))

    with _make_progress() as progress:
        task = progress.add_task("Сжатие...", total=len(files))

        for file_path in files:
            output_path = resolved_output / file_path.relative_to(resolved_input)

            if output_path.exists() and not overwrite:
                stats.skipped += 1
                progress.advance(task)
                continue

            result = processor.compress_image(
                source=file_path,
                output=output_path,
                quality=quality,
                max_width=max_width,
                max_height=max_height,
                strip_metadata=strip_metadata,
            )
            stats.results.append(result)

            if result.success:
                stats.processed += 1
                stats.total_input_bytes += result.original_size
                stats.total_output_bytes += result.output_size
            else:
                stats.errors += 1

            progress.advance(task)

    _print_stats(stats, log_file)


@app.command()
def image_convert(
    ctx: typer.Context,
    input_folder: Optional[Path] = typer.Option(
        None, "--input", "-i", help="Папка с изображениями"
    ),
    output_folder: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Папка для результатов"
    ),
    target_format: str = typer.Option(
        "webp", "--format", "-f", help="Целевой формат: webp, jpg, png"
    ),
    quality: int = typer.Option(
        85, "--quality", "-q", help="Качество для lossy форматов (1-100)", min=1, max=100
    ),
    strip_metadata: bool = typer.Option(
        True, "--strip-metadata/--keep-metadata", help="Удалить EXIF метаданные"
    ),
    glob_pattern: str = typer.Option(
        "*.{jpg,jpeg,png,gif,bmp,tiff}", "--glob", "-g", help="Паттерн файлов"
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Искать в подпапках"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Перезаписать существующие"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Показать что будет сделано"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный вывод"),
):
    """
    Конвертация изображений в другой формат (по умолчанию WebP).

    Примеры:

        wp-bulk image-convert -P ost --format webp

        wp-bulk image-convert --input ./photos -f webp -q 85
    """
    valid_formats = {"webp", "jpg", "jpeg", "png"}
    if target_format.lower() not in valid_formats:
        console.print(
            f"[red]ERROR: Неподдерживаемый формат '{target_format}'. "
            f"Доступные: {', '.join(sorted(valid_formats))}[/red]"
        )
        raise typer.Exit(1)

    resolved_input = _resolve_input_folder(ctx, input_folder)
    resolved_output = _resolve_output_folder(
        resolved_input, output_folder, target_format.lower()
    )

    if not resolved_input.exists():
        console.print(f"[red]ERROR: Папка не найдена: {resolved_input}[/red]")
        raise typer.Exit(1)

    log_file = _setup_logging(ctx, "image_convert", verbose)

    console.print(f"\n[bold blue]Image Convert[/bold blue]")
    console.print("=" * 60)
    console.print(f"  Input:    [cyan]{resolved_input}[/cyan]")
    console.print(f"  Output:   [cyan]{resolved_output}[/cyan]")
    console.print(f"  Format:   [cyan]{target_format.upper()}[/cyan]")
    console.print(f"  Quality:  [cyan]{quality}[/cyan]")
    console.print()

    processor = ImageProcessor()
    files = processor.collect_files(resolved_input, glob_pattern, recursive)

    if not files:
        console.print("[yellow]Изображения не найдены[/yellow]")
        return

    console.print(f"Найдено [cyan]{len(files)}[/cyan] изображений\n")

    if dry_run:
        for f in files[:20]:
            out_name = f"{f.stem}.{target_format.lower()}"
            console.print(f"  [dim]{f.name} -> {out_name}[/dim]")
        if len(files) > 20:
            console.print(f"  ... и ещё {len(files) - 20}")
        console.print("\n[yellow]DRY-RUN: изменения не внесены[/yellow]")
        return

    stats = BatchProcessingStats(total=len(files))

    with _make_progress() as progress:
        task = progress.add_task("Конвертация...", total=len(files))

        for file_path in files:
            rel_path = file_path.relative_to(resolved_input)
            output_path = resolved_output / rel_path.with_suffix(
                f".{target_format.lower()}"
            )

            if output_path.exists() and not overwrite:
                stats.skipped += 1
                progress.advance(task)
                continue

            result = processor.convert_image(
                source=file_path,
                output=output_path,
                target_format=target_format,
                quality=quality,
                strip_metadata=strip_metadata,
            )
            stats.results.append(result)

            if result.success:
                stats.processed += 1
                stats.total_input_bytes += result.original_size
                stats.total_output_bytes += result.output_size
            else:
                stats.errors += 1

            progress.advance(task)

    _print_stats(stats, log_file)
