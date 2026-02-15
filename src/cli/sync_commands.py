"""
Команды синхронизации: sync, async
"""

from pathlib import Path
from typing import Optional, Dict
import asyncio
from datetime import datetime

import pandas as pd
from loguru import logger

from . import app, console
from .helpers import get_project, resolve_env_file
from .common import handle_command_errors, load_and_print_credentials
from ..config import AppConfig, SyncConfig, PathsConfig, LoggingConfig, WordPressConfig
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..wordpress.client import WordPressClient
from ..wordpress.taxonomies import TaxonomyCache
from ..sync.content_sync import ContentSync
from ..sync.content_sync_async import ContentSyncAsync
from ..sync.media_sync import MediaSync
from ..sync.taxonomy_sync import TaxonomySync
from ..sync.taxonomy_sync_async import TaxonomySyncAsync
from ..utils.logger import setup_logger, log_summary
from ..utils.auth import load_credentials_from_env, load_async_config_from_env
from ..exceptions import WPBulkEditorError

import typer

from .project import ProjectContext


def _resolve_file(proj: ProjectContext, file: Path) -> Path:
    """Резолвинг Excel файла: если не абсолютный и не существует — ищем в проекте."""
    if file.is_absolute() and file.exists():
        return file
    if file.exists():
        return file.resolve()
    # Пробуем относительно проекта
    if proj.active:
        project_path = proj.root / file
        if project_path.exists():
            return project_path
    # Ничего не найдено — возвращаем как есть, ошибка вылетит дальше
    console.print(f"[red]ERROR: Файл не найден: {file}[/red]")
    if proj.active:
        console.print(f"[dim]Проверены пути: {file}, {proj.root / file}[/dim]")
    raise typer.Exit(code=1)


@app.command()
def sync(
    ctx: typer.Context,
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу",
    ),
    domain: Optional[str] = typer.Option(
        None,
        "--domain",
        help="WordPress domain (например, https://example.com)"
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        help="WordPress username"
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        help="WordPress Application Password"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов через запятую (posts,pages)"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Режим dry-run (без изменений)"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
    concurrency: int = typer.Option(
        5,
        "--concurrency",
        help="Количество параллельных запросов"
    ),
    skip_media: bool = typer.Option(
        False,
        "--skip-media",
        help="Пропустить загрузку медиа"
    ),
    skip_tax: bool = typer.Option(
        False,
        "--skip-tax",
        help="Пропустить обновление таксономий (категории, теги)"
    ),
    no_update_excel: bool = typer.Option(
        False,
        "--no-update-excel",
        help="Не записывать ID обратно в Excel"
    ),
    delete_media: bool = typer.Option(
        False,
        "--delete-media",
        help="Удалять featured media при удалении записей (action=delete)"
    ),
    skip_existing_media: bool = typer.Option(
        False,
        "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update (ускоряет массовое редактирование)"
    ),
    test_mode: bool = typer.Option(
        False,
        "--test-mode",
        help="Тестовый режим: обрабатывает только первые 3 строки из одного листа"
    ),
):
    """
    Синхронизация Excel -> WordPress
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    file = _resolve_file(proj, file)

    console.print(f"\n[bold blue]WordPress Bulk Editor - Sync[/bold blue]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    # Валидация test_mode
    if test_mode:
        if not sheets:
            console.print("[bold red]ERROR: Тестовый режим (--test-mode) требует указания одного листа через --sheets[/bold red]\n")
            raise typer.Exit(code=1)
        sheets_list = [s.strip() for s in sheets.split(",")]
        if len(sheets_list) > 1:
            console.print("[bold red]ERROR: Тестовый режим (--test-mode) работает только с одним листом[/bold red]\n")
            raise typer.Exit(code=1)
        console.print("[bold yellow]ТЕСТОВЫЙ РЕЖИМ: Будут обработаны только первые 3 строки[/bold yellow]\n")

    with handle_command_errors():
        # 1. Загружаем credentials (приоритет: CLI параметры -> .env файл)
        domain, username, password = load_and_print_credentials(
            env_file=env_file,
            domain=domain,
            username=username,
            password=password,
        )

        # 2. Создаем конфигурацию
        config = AppConfig.from_excel(
            excel_file=file,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
            project_root=proj.root,
            sync=SyncConfig(
                dry_run=dry_run,
                concurrency=concurrency,
                skip_media=skip_media,
                skip_tax=skip_tax,
                update_excel=not no_update_excel,
                delete_featured_media=delete_media,
                skip_existing_media=skip_existing_media
            ),
            logging=LoggingConfig(
                level="DEBUG" if verbose else "INFO"
            )
        )

        # 3. Настройка логирования
        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="sync"
        )

        # 4. Запускаем синхронизацию
        asyncio.run(run_sync(config, sheets, test_mode=test_mode))

        console.print(f"\n[bold green]SUCCESS! Синхронизация завершена![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")


async def run_sync(config: AppConfig, sheets_filter: Optional[str] = None, test_mode: bool = False):
    """
    Основная логика синхронизации.
    """
    started_at = datetime.now()

    stats = {
        "file": str(config.paths.excel_file),
        "started_at": started_at.isoformat(),
        "sheets": {},
        "overall": {
            "total": 0,
            "successful": 0,
            "errors": 0,
            "success_rate": 0.0
        }
    }

    try:
        with ExcelReader(config.paths.excel_file) as reader:
            sheets_by_type = reader.get_sheets_by_type()

            logger.info(f"Найдено листов:")
            logger.info(f"  Content: {len(sheets_by_type['content'])}")
            logger.info(f"  Taxonomy: {len(sheets_by_type['taxonomy'])}")

            sheets_to_process = []
            if sheets_filter:
                filter_list = [s.strip() for s in sheets_filter.split(",")]
                sheets_to_process = filter_list
            else:
                sheets_to_process = (
                    sheets_by_type["content"] +
                    sheets_by_type["taxonomy"]
                )

            logger.info(f"Листы для обработки: {sheets_to_process}")

            async with WordPressClient(config.wordpress) as wp_client:
                await wp_client.test_connection()

                logger.info("Загрузка кэша таксономий...")
                taxonomy_cache = TaxonomyCache()

                taxonomies_to_cache = set()
                for sheet_name in sheets_to_process:
                    sheet_type = reader.classify_sheet(sheet_name)
                    if sheet_type == "content":
                        df = reader.read_sheet(sheet_name)
                        for col in df.columns:
                            if col in ["categories", "tags"] or col.endswith("-cat"):
                                taxonomies_to_cache.add(col)
                    elif sheet_type == "taxonomy":
                        taxonomies_to_cache.add(sheet_name)

                if taxonomies_to_cache:
                    await taxonomy_cache.load(wp_client, list(taxonomies_to_cache))

                content_sync = ContentSync(wp_client, config, taxonomy_cache)
                media_sync = MediaSync(wp_client, config)
                taxonomy_sync = TaxonomySync(wp_client, config)

                excel_writer = None
                if config.sync.update_excel:
                    excel_writer = ExcelWriter(
                        config.paths.excel_file,
                        taxonomy_cache=taxonomy_cache
                    )
                    excel_writer.load()

                for sheet_name in sheets_to_process:
                    logger.info(f"\n{'='*50}")
                    logger.info(f"Обработка листа: {sheet_name}")
                    logger.info(f"{'='*50}")

                    sheet_stats = await process_sheet(
                        reader=reader,
                        content_sync=content_sync,
                        media_sync=media_sync,
                        taxonomy_sync=taxonomy_sync,
                        excel_writer=excel_writer,
                        sheet_name=sheet_name,
                        config=config,
                        test_mode=test_mode
                    )

                    stats["sheets"][sheet_name] = sheet_stats
                    stats["overall"]["total"] += sheet_stats.get("total", 0)
                    stats["overall"]["successful"] += (
                        sheet_stats.get("added", 0) +
                        sheet_stats.get("updated", 0) +
                        sheet_stats.get("deleted", 0)
                    )
                    stats["overall"]["errors"] += sheet_stats.get("errors", 0)

                if excel_writer:
                    excel_writer.save()

        total_skipped = sum(
            sheet.get("skipped", 0) for sheet in stats["sheets"].values()
        )
        processed = stats["overall"]["total"] - total_skipped
        if processed > 0:
            stats["overall"]["success_rate"] = (
                stats["overall"]["successful"] / processed
            ) * 100
        elif stats["overall"]["total"] > 0:
            stats["overall"]["success_rate"] = 100.0

        finished_at = datetime.now()
        stats["finished_at"] = finished_at.isoformat()
        stats["duration_seconds"] = (finished_at - started_at).total_seconds()

        log_summary(stats, config.paths.logs_folder / "sync.log")

    except Exception as e:
        logger.exception("Ошибка синхронизации")
        raise


async def process_sheet(
    reader: ExcelReader,
    content_sync: ContentSync,
    media_sync,
    taxonomy_sync: TaxonomySync,
    excel_writer: Optional[ExcelWriter],
    sheet_name: str,
    config: AppConfig,
    test_mode: bool = False
) -> dict:
    """
    Обработка одного листа.
    """
    try:
        df = reader.read_sheet(sheet_name)
        logger.info(f"Всего строк: {len(df)}")

        if test_mode:
            original_len = len(df)
            df = df.head(3)
            logger.info(f"ТЕСТОВЫЙ РЕЖИМ: Обрабатываем только первые {len(df)} строк из {original_len}")

        if "action" not in df.columns:
            logger.warning(f"Лист {sheet_name} не содержит колонку 'action', пропускаем")
            return {
                "total": len(df), "added": 0, "updated": 0,
                "deleted": 0, "skipped": len(df), "errors": 0
            }

        sheet_type = reader.classify_sheet(sheet_name)

        if sheet_type == "content":
            stats = await content_sync.sync_sheet(sheet_name, df, excel_writer)
            stats["total"] = len(df)
            return stats
        elif sheet_type == "media":
            stats = await media_sync.sync_media(df, excel_writer)
            stats["total"] = len(df)
            return stats
        elif sheet_type == "taxonomy":
            stats = await taxonomy_sync.sync_sheet(sheet_name, df, excel_writer)
            stats["total"] = len(df)
            return stats
        else:
            logger.warning(f"Неизвестный тип листа: {sheet_name}")
            return {
                "total": len(df), "added": 0, "updated": 0,
                "deleted": 0, "skipped": len(df), "errors": 0
            }

    except Exception as e:
        logger.error(f"Ошибка обработки листа {sheet_name}: {e}")
        logger.exception(e)
        return {
            "total": 0, "added": 0, "updated": 0,
            "deleted": 0, "skipped": 0, "errors": 1
        }


# ============================================================
# Async sync command
# ============================================================

@app.command(name="async")
def async_sync(
    ctx: typer.Context,
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу",
    ),
    domain: Optional[str] = typer.Option(
        None,
        "--domain",
        help="WordPress domain (например, https://example.com)"
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        help="WordPress username"
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        help="WordPress Application Password"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов через запятую (posts,pages)"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Режим dry-run (без изменений)"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        help="Количество параллельных запросов (default: 5)"
    ),
    api_timeout: Optional[int] = typer.Option(
        None,
        "--api-timeout",
        help="API timeout в секундах (default: 30)"
    ),
    max_retries: Optional[int] = typer.Option(
        None,
        "--max-retries",
        help="Максимум повторных попыток (default: 3)"
    ),
    enable_circuit_breaker: bool = typer.Option(
        True,
        "--circuit-breaker/--no-circuit-breaker",
        help="Включить circuit breaker"
    ),
    skip_media: bool = typer.Option(
        False,
        "--skip-media",
        help="Пропустить загрузку медиа"
    ),
    skip_tax: bool = typer.Option(
        False,
        "--skip-tax",
        help="Пропустить обновление таксономий (категории, теги)"
    ),
    no_update_excel: bool = typer.Option(
        False,
        "--no-update-excel",
        help="Не записывать ID обратно в Excel"
    ),
    delete_media: bool = typer.Option(
        False,
        "--delete-media",
        help="Удалять featured media при удалении записей (action=delete)"
    ),
    skip_existing_media: bool = typer.Option(
        False,
        "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update (ускоряет массовое редактирование)"
    ),
    test_mode: bool = typer.Option(
        False,
        "--test-mode",
        help="Тестовый режим: обрабатывает только первые 3 строки"
    ),
):
    """
     Асинхронная синхронизация Excel -> WordPress

    Отличия от sync:
    - Параллельная обработка строк (Semaphore)
    - Retry с exponential backoff
    - Circuit breaker для устойчивости
    - Настраиваемые таймауты
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    file = _resolve_file(proj, file)

    console.print(f"\n[bold cyan] WordPress Bulk Editor - Async Sync[/bold cyan]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    if test_mode:
        if not sheets:
            console.print("[bold red]ERROR: Тестовый режим (--test-mode) требует указания одного листа через --sheets[/bold red]\n")
            raise typer.Exit(code=1)
        sheets_list = [s.strip() for s in sheets.split(",")]
        if len(sheets_list) > 1:
            console.print("[bold red]ERROR: Тестовый режим (--test-mode) работает только с одним листом[/bold red]\n")
            raise typer.Exit(code=1)
        console.print("[bold yellow]ТЕСТОВЫЙ РЕЖИМ: Будут обработаны только первые 3 строки[/bold yellow]\n")

    with handle_command_errors():
        domain, username, password = load_and_print_credentials(
            env_file=env_file,
            domain=domain,
            username=username,
            password=password,
        )

        concurrency_val, retry_cfg, circuit_breaker_obj = load_async_config_from_env(
            env_file=env_file,
            cli_concurrency=concurrency,
            cli_max_retries=max_retries
        )

        if not enable_circuit_breaker:
            circuit_breaker_obj = None

        config = AppConfig.from_excel(
            excel_file=file,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
            project_root=proj.root,
            sync=SyncConfig(
                dry_run=dry_run,
                concurrency=concurrency_val,
                skip_media=skip_media,
                skip_tax=skip_tax,
                update_excel=not no_update_excel,
                delete_featured_media=delete_media,
                skip_existing_media=skip_existing_media,
                timeout=api_timeout or 30
            ),
            logging=LoggingConfig(
                level="DEBUG" if verbose else "INFO"
            )
        )

        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="async_sync"
        )

        asyncio.run(run_sync_async(
            config=config,
            sheets_filter=sheets,
            test_mode=test_mode,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj
        ))

        console.print(f"\n[bold green]SUCCESS! Асинхронная синхронизация завершена![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")


async def run_sync_async(
    config: AppConfig,
    sheets_filter: Optional[str] = None,
    test_mode: bool = False,
    retry_config=None,
    circuit_breaker=None
):
    """
    Async версия run_sync с параллельной обработкой строк.
    """
    started_at = datetime.now()

    stats = {
        "file": str(config.paths.excel_file),
        "started_at": started_at.isoformat(),
        "sheets": {},
        "overall": {
            "total": 0,
            "successful": 0,
            "errors": 0,
            "success_rate": 0.0
        }
    }

    try:
        with ExcelReader(config.paths.excel_file) as reader:
            sheets_by_type = reader.get_sheets_by_type()

            logger.info(f"Найдено листов:")
            logger.info(f"  Content: {len(sheets_by_type['content'])}")
            logger.info(f"  Taxonomy: {len(sheets_by_type['taxonomy'])}")

            sheets_to_process = []
            if sheets_filter:
                filter_list = [s.strip() for s in sheets_filter.split(",")]
                sheets_to_process = filter_list
            else:
                sheets_to_process = (
                    sheets_by_type["content"] +
                    sheets_by_type["taxonomy"]
                )

            logger.info(f"Листы для обработки: {sheets_to_process}")

            timeout = config.sync.timeout if hasattr(config.sync, 'timeout') else 30

            async with WordPressClient(
                config.wordpress,
                retry_config=retry_config,
                circuit_breaker=circuit_breaker,
                timeout=timeout
            ) as wp_client:
                await wp_client.test_connection()

                logger.info("Загрузка кэша таксономий...")
                taxonomy_cache = TaxonomyCache()

                taxonomies_to_cache = set()
                for sheet_name in sheets_to_process:
                    sheet_type = reader.classify_sheet(sheet_name)
                    if sheet_type == "content":
                        df = reader.read_sheet(sheet_name)
                        for col in df.columns:
                            if col in ["categories", "tags"] or col.endswith("-cat"):
                                taxonomies_to_cache.add(col)
                    elif sheet_type == "taxonomy":
                        taxonomies_to_cache.add(sheet_name)

                if taxonomies_to_cache:
                    await taxonomy_cache.load(wp_client, list(taxonomies_to_cache))

                content_sync = ContentSyncAsync(wp_client, config, taxonomy_cache)
                taxonomy_sync = TaxonomySyncAsync(wp_client, config)
                media_sync = MediaSync(wp_client, config)

                excel_writer = None
                if config.sync.update_excel:
                    excel_writer = ExcelWriter(
                        config.paths.excel_file,
                        taxonomy_cache=taxonomy_cache
                    )
                    excel_writer.load()

                for sheet_name in sheets_to_process:
                    logger.info(f"\n{'='*50}")
                    logger.info(f"Обработка листа: {sheet_name}")
                    logger.info(f"{'='*50}")

                    sheet_stats = await process_sheet_async(
                        reader=reader,
                        content_sync=content_sync,
                        media_sync=media_sync,
                        taxonomy_sync=taxonomy_sync,
                        excel_writer=excel_writer,
                        sheet_name=sheet_name,
                        config=config,
                        test_mode=test_mode
                    )

                    stats["sheets"][sheet_name] = sheet_stats
                    stats["overall"]["total"] += sheet_stats.get("total", 0)
                    stats["overall"]["successful"] += (
                        sheet_stats.get("added", 0) +
                        sheet_stats.get("updated", 0) +
                        sheet_stats.get("deleted", 0)
                    )
                    stats["overall"]["errors"] += sheet_stats.get("errors", 0)

                if excel_writer:
                    excel_writer.save()

        total_skipped = sum(
            sheet.get("skipped", 0) for sheet in stats["sheets"].values()
        )
        processed = stats["overall"]["total"] - total_skipped
        if processed > 0:
            stats["overall"]["success_rate"] = (
                stats["overall"]["successful"] / processed
            ) * 100
        elif stats["overall"]["total"] > 0:
            stats["overall"]["success_rate"] = 100.0

        finished_at = datetime.now()
        stats["finished_at"] = finished_at.isoformat()
        stats["duration_seconds"] = (finished_at - started_at).total_seconds()

        log_summary(stats, config.paths.logs_folder / "async_sync.log")

    except Exception as e:
        logger.exception("Ошибка async синхронизации")
        raise


async def process_sheet_async(
    reader: ExcelReader,
    content_sync: ContentSyncAsync,
    media_sync,
    taxonomy_sync: TaxonomySyncAsync,
    excel_writer: Optional[ExcelWriter],
    sheet_name: str,
    config: AppConfig,
    test_mode: bool = False
) -> dict:
    """
    Async версия process_sheet.
    """
    try:
        df = reader.read_sheet(sheet_name)
        logger.info(f"Всего строк: {len(df)}")

        if test_mode:
            original_len = len(df)
            df = df.head(3)
            logger.info(f"ТЕСТОВЫЙ РЕЖИМ: Обрабатываем только первые {len(df)} строк из {original_len}")

        if "action" not in df.columns:
            logger.warning(f"Лист {sheet_name} не содержит колонку 'action', пропускаем")
            return {
                "total": len(df), "added": 0, "updated": 0,
                "deleted": 0, "skipped": len(df), "errors": 0
            }

        sheet_type = reader.classify_sheet(sheet_name)

        if sheet_type == "content":
            stats = await content_sync.sync_sheet(sheet_name, df, excel_writer)
            stats["total"] = len(df)
            return stats
        elif sheet_type == "media":
            stats = await media_sync.sync_media(df, excel_writer)
            stats["total"] = len(df)
            return stats
        elif sheet_type == "taxonomy":
            stats = await taxonomy_sync.sync_sheet(sheet_name, df, excel_writer)
            stats["total"] = len(df)
            return stats
        else:
            logger.warning(f"Неизвестный тип листа: {sheet_name}")
            return {
                "total": len(df), "added": 0, "updated": 0,
                "deleted": 0, "skipped": len(df), "errors": 0
            }

    except Exception as e:
        logger.error(f"Ошибка обработки листа {sheet_name}: {e}")
        logger.exception(e)
        return {
            "total": 0, "added": 0, "updated": 0,
            "deleted": 0, "skipped": 0, "errors": 1
        }
