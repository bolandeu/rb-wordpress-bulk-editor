"""
Команды экспорта: export, export-media
"""

from pathlib import Path
from typing import Optional, Dict
import asyncio

from loguru import logger

from . import app, console
from .helpers import get_project, resolve_env_file, resolve_logs_folder, resolve_output
from .common import handle_command_errors, load_and_print_credentials, setup_command_logging
from ..config import LoggingConfig, WordPressConfig, Settings
from ..wordpress.client import WordPressClient
from ..utils.content_exporter import ContentExporter

import typer


@app.command()
def export(
    ctx: typer.Context,
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="WordPress domain"),
    username: Optional[str] = typer.Option(None, "--username", "-u", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="WordPress Application Password"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    output: Path = typer.Option(Path("exported_content"), "--output", "-o", help="Папка для экспорта контента"),
    concurrency: int = typer.Option(10, "--concurrency", "-c", help="Количество параллельных загрузок"),
    include_media: bool = typer.Option(False, "--include-media", help="Включить экспорт метаданных медиа"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи")
):
    """
    Экспорт всего контента сайта в файлы.

    Находит все типы контента и экспортирует их в отдельные HTML файлы.
    Создает CSV индекс.
    """
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "exported_content")

    with handle_command_errors():
        log_file = setup_command_logging(ctx, "export", verbose)

        console.print("\n[bold cyan]WordPress Content Exporter[/bold cyan]")
        console.print("=" * 60 + "\n")

        domain, username, password = load_and_print_credentials(
            env_file=env_file, domain=domain,
            username=username, password=password,
        )

        console.print(f"[yellow]->[/yellow] Папка экспорта: {output}")
        console.print(f"[yellow]->[/yellow] Параллельных загрузок: {concurrency}")
        if include_media:
            console.print(f"[yellow]->[/yellow] Экспорт медиа: Да (только метаданные)")
        console.print()

        wp_config = WordPressConfig.from_credentials(domain, username, password)

        stats = asyncio.run(export_content(wp_config, output, concurrency, include_media))

        console.print("\n[bold green]Экспорт завершен![/bold green]")
        console.print("=" * 60)

        total = 0
        for post_type, count in stats.items():
            console.print(f"  {post_type}: [cyan]{count}[/cyan] записей")
            total += count

        console.print(f"\n[bold]Всего экспортировано:[/bold] [green]{total}[/green] записей")
        console.print(f"[bold]CSV индекс:[/bold] {output / 'index.csv'}")
        console.print(f"\n[dim]Логи: {log_file}[/dim]")


async def export_content(
    wp_config: WordPressConfig, output_folder: Path,
    concurrency: int = 10, include_media: bool = False
) -> Dict[str, int]:
    """Основная функция экспорта контента."""
    async with WordPressClient(wp_config) as client:
        settings = Settings()
        exporter = ContentExporter(client, output_folder, concurrency,
                                   skip_post_type_patterns=settings.get_skip_post_type_patterns())
        logger.info("Начало экспорта контента...")
        stats = await exporter.export_all(include_media=include_media)
        logger.info("Экспорт завершен")
        return stats


@app.command()
def export_media(
    ctx: typer.Context,
    domain: Optional[str] = typer.Option(None, "--domain", help="URL WordPress сайта"),
    username: Optional[str] = typer.Option(None, "--username", help="Имя пользователя WordPress"),
    password: Optional[str] = typer.Option(None, "--password", help="Application Password"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    output: Path = typer.Option(Path("export_media"), "--output", "-o", help="Папка для экспорта"),
    download_files: bool = typer.Option(True, "--download-files/--no-download-files",
        help="Скачивать файлы или только метаданные"),
    concurrency: int = typer.Option(10, "--concurrency", help="Количество параллельных загрузок"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
):
    """
    Экспортировать медиафайлы из WordPress в CSV (с файлами или без)
    """
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "export_media")

    console.print(f"\n[bold blue]WordPress Bulk Editor - Export Media[/bold blue]\n")
    console.print(f"Папка экспорта: [cyan]{output}[/cyan]\n")

    with handle_command_errors():
        domain, username, password = load_and_print_credentials(
            env_file=env_file, domain=domain,
            username=username, password=password,
        )

        wp_config = WordPressConfig.from_credentials(domain, username, password)
        log_file = setup_command_logging(ctx, "export_media", verbose)

        console.print(f"[green]Скачивание файлов:[/green] {'Да' if download_files else 'Нет'}")
        console.print(f"[green]Параллельность:[/green] {concurrency}\n")

        count = asyncio.run(run_export_media(wp_config, output, download_files, concurrency))

        console.print(f"\n[bold green]SUCCESS! Экспорт завершен![/bold green]")
        console.print(f"Экспортировано: [cyan]{count}[/cyan] медиафайлов")
        console.print(f"CSV индекс: [cyan]{output / 'media_index.csv'}[/cyan]")
        console.print(f"\n[dim]Логи: {log_file}[/dim]")


async def run_export_media(
    wp_config: WordPressConfig, output_folder: Path,
    download_files: bool, concurrency: int
) -> int:
    """Основная функция экспорта медиа."""
    from ..wordpress.media import MediaEndpoint
    from ..utils.media_exporter import MediaExporter

    async with WordPressClient(wp_config) as client:
        media_endpoint = MediaEndpoint(client)
        media_exporter = MediaExporter(
            media_endpoint, output_folder,
            download_files=download_files, concurrency=concurrency
        )
        logger.info("Начало экспорта медиафайлов...")
        count = await media_exporter.export_all_media()
        logger.info("Экспорт завершен")
        return count
