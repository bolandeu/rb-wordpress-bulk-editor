"""
Команды синхронизации: sync, async
"""

from pathlib import Path
from typing import Optional
import asyncio
from datetime import datetime

from loguru import logger

from . import app, console
from .helpers import get_project, resolve_env_file, require_excel_file
from .common import handle_command_errors, load_and_print_credentials, parse_comma_list
from .. import output
from ..config import AppConfig, SyncConfig, LoggingConfig
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..wordpress.client import WordPressClient
from ..wordpress.taxonomies import TaxonomyCache
from ..records.excel import ExcelRecordSource, ExcelRecordWriter
from ..sync.content_sync import ContentSync
from ..sync.content_sync_async import ContentSyncAsync
from ..sync.media_sync import MediaSync
from ..sync.orchestrator import sync_record_groups
from ..sync.taxonomy_sync import TaxonomySync
from ..sync.taxonomy_sync_async import TaxonomySyncAsync
from ..utils.logger import setup_logger, log_summary
from ..utils.auth import load_async_config_from_env
from ..constants import is_taxonomy_column

import typer


# ============================================================
# Shared helpers
# ============================================================

def _validate_test_mode(test_mode: bool, sheets: Optional[str]):
    """Валидация --test-mode (общая для sync и async)."""
    if not test_mode:
        return
    if not sheets:
        console.print("[bold red]ERROR: Тестовый режим (--test-mode) требует указания одного листа через --sheets[/bold red]\n")
        raise typer.Exit(code=1)
    sheets_list = [s.strip() for s in sheets.split(",")]
    if len(sheets_list) > 1:
        console.print("[bold red]ERROR: Тестовый режим (--test-mode) работает только с одним листом[/bold red]\n")
        raise typer.Exit(code=1)
    console.print("[bold yellow]ТЕСТОВЫЙ РЕЖИМ: Будут обработаны только первые 3 строки[/bold yellow]\n")


def _build_sync_config(
    dry_run: bool,
    concurrency: int,
    skip_media: bool,
    skip_tax: bool,
    no_update_excel: bool,
    delete_media: bool,
    skip_existing_media: bool,
    columns: Optional[str],
    timeout: int = 30,
) -> SyncConfig:
    """Создание SyncConfig (общее для sync и async)."""
    return SyncConfig(
        dry_run=dry_run,
        concurrency=concurrency,
        skip_media=skip_media,
        skip_tax=skip_tax,
        update_excel=not no_update_excel,
        delete_featured_media=delete_media,
        skip_existing_media=skip_existing_media,
        timeout=timeout,
        columns_filter=parse_comma_list(columns),
    )


def _empty_stats(total: int = 0, skipped: int = 0, errors: int = 0) -> dict:
    """Пустая статистика для листа."""
    return {
        "total": total, "added": 0, "updated": 0,
        "deleted": 0, "skipped": skipped, "errors": errors,
    }


# ============================================================
# Unified sync engine (used by both sync and async commands)
# ============================================================

async def _run_sync_engine(
    config: AppConfig,
    sheets_filter: Optional[str] = None,
    test_mode: bool = False,
    content_sync_cls=ContentSync,
    taxonomy_sync_cls=TaxonomySync,
    retry_config=None,
    circuit_breaker=None,
    command_name: str = "sync",
):
    """
    Единый движок синхронизации для sync и async команд.

    Различия параметризованы:
    - content_sync_cls: ContentSync или ContentSyncAsync
    - taxonomy_sync_cls: TaxonomySync или TaxonomySyncAsync
    - retry_config / circuit_breaker: только для async
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
        from ..config import Settings
        with ExcelReader(config.paths.excel_file, known_taxonomies=Settings().get_taxonomies()) as reader:
            sheets_by_type = reader.get_sheets_by_type()

            logger.info(f"Найдено листов:")
            logger.info(f"  Content: {len(sheets_by_type['content'])}")
            logger.info(f"  Taxonomy: {len(sheets_by_type['taxonomy'])}")

            sheets_to_process = []
            if sheets_filter:
                sheets_to_process = [s.strip() for s in sheets_filter.split(",")]
            else:
                taxonomy_sheets = [] if config.sync.skip_tax else sheets_by_type["taxonomy"]
                sheets_to_process = sheets_by_type["content"] + taxonomy_sheets

            if config.sync.skip_tax:
                logger.info("--skip-tax: taxonomy-листы исключены из обработки")
            if config.sync.skip_media:
                logger.info("--skip-media: загрузка медиа пропускается")
            logger.info(f"Листы для обработки: {sheets_to_process}")

            # WordPressClient — async версия передаёт retry/circuit_breaker/timeout
            wp_kwargs = {"config": config.wordpress}
            if retry_config is not None:
                wp_kwargs["retry_config"] = retry_config
            if circuit_breaker is not None:
                wp_kwargs["circuit_breaker"] = circuit_breaker
            if retry_config is not None or circuit_breaker is not None:
                wp_kwargs["timeout"] = config.sync.timeout

            async with WordPressClient(**wp_kwargs) as wp_client:
                await wp_client.test_connection()

                # Загрузка кэша таксономий
                logger.info("Загрузка кэша таксономий...")
                taxonomy_cache = TaxonomyCache()

                taxonomies_to_cache = set()
                if not config.sync.skip_tax:
                    for sheet_name in sheets_to_process:
                        sheet_type = reader.classify_sheet(sheet_name)
                        if sheet_type == "content":
                            df = reader.read_sheet(sheet_name)
                            for col in df.columns:
                                if is_taxonomy_column(col):
                                    taxonomies_to_cache.add(col)
                        elif sheet_type == "taxonomy":
                            taxonomies_to_cache.add(sheet_name)

                if taxonomies_to_cache:
                    await taxonomy_cache.load(wp_client, list(taxonomies_to_cache))

                # Создаём sync-объекты (параметризовано через cls)
                content_sync = content_sync_cls(wp_client, config, taxonomy_cache)
                media_sync = MediaSync(wp_client, config)
                taxonomy_sync = taxonomy_sync_cls(wp_client, config)

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

                    sheet_stats = await _process_sheet(
                        reader=reader,
                        content_sync=content_sync,
                        media_sync=media_sync,
                        taxonomy_sync=taxonomy_sync,
                        excel_writer=excel_writer,
                        sheet_name=sheet_name,
                        config=config,
                        test_mode=test_mode,
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

        log_summary(stats, config.paths.logs_folder / f"{command_name}.log")
        return stats

    except Exception as e:
        logger.exception(f"Ошибка {command_name}")
        raise


async def _process_sheet(
    reader: ExcelReader,
    content_sync,
    media_sync,
    taxonomy_sync,
    excel_writer: Optional[ExcelWriter],
    sheet_name: str,
    config: AppConfig,
    test_mode: bool = False,
) -> dict:
    """Обработка одного листа (единая для sync и async)."""
    try:
        source = ExcelRecordSource(reader, sheet_name)
        fields = source.fields()
        records = list(source.read())
        logger.info(f"Всего строк: {len(records)}")

        if test_mode:
            original_len = len(records)
            records = records[:3]
            logger.info(f"ТЕСТОВЫЙ РЕЖИМ: Обрабатываем только первые {len(records)} строк из {original_len}")

        if "action" not in fields:
            logger.warning(f"Лист {sheet_name} не содержит колонку 'action', пропускаем")
            return _empty_stats(total=len(records), skipped=len(records))

        writer = ExcelRecordWriter(excel_writer, sheet_name) if excel_writer else None
        sheet_type = source.sheet_type

        if sheet_type in ("content", "taxonomy"):
            if sheet_type == "taxonomy" and config.sync.skip_tax:
                logger.info(f"--skip-tax: пропускаем лист '{sheet_name}'")
                return _empty_stats(total=len(records), skipped=len(records))
            report = await sync_record_groups(records, content_sync, taxonomy_sync, writer)
            return report.to_stats()
        elif sheet_type == "media":
            if config.sync.skip_media:
                logger.info(f"--skip-media: пропускаем лист '{sheet_name}'")
                return _empty_stats(total=len(records), skipped=len(records))
            report = await media_sync.sync_media(records, writer)
            return report.to_stats()
        else:
            logger.warning(f"Неизвестный тип листа: {sheet_name}")
            return _empty_stats(total=len(records), skipped=len(records))

    except Exception as e:
        logger.exception(f"Ошибка обработки листа {sheet_name}")
        return _empty_stats(errors=1)


# ============================================================
# Sync command
# ============================================================

@app.command()
@output.guard
def sync(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Путь к Excel файлу (авто-определяется из --project)",
    ),
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain (например, https://example.com)"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", help="WordPress Application Password"),
    sheets: Optional[str] = typer.Option(None, "--sheets", help="Список листов через запятую (posts,pages)"),
    env_file: Optional[Path] = typer.Option(
        None, "--env-file", help="Путь к .env файлу (по умолчанию .env)",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True,
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run (без изменений)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: int = typer.Option(5, "--concurrency", help="Количество параллельных запросов"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить загрузку медиа"),
    skip_tax: bool = typer.Option(False, "--skip-tax", help="Пропустить обновление таксономий (категории, теги)"),
    no_update_excel: bool = typer.Option(False, "--no-update-excel", help="Не записывать ID обратно в Excel"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять featured media при удалении записей (action=delete)"),
    skip_existing_media: bool = typer.Option(False, "--skip-existing-media", help="Не прикреплять существующие featured_media при update"),
    test_mode: bool = typer.Option(False, "--test-mode", help="Тестовый режим: обрабатывает только первые 3 строки из одного листа"),
    columns: Optional[str] = typer.Option(None, "--columns", help="Колонки для обновления через запятую (excerpt,seo_title)"),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Синхронизация Excel -> WordPress.

    Exit-коды: 0 — успех, 2 — аргументы/структура файла, 3 — нет credentials,
    5 — недоступен WordPress, 1 — непредвиденная ошибка.
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    file = require_excel_file(ctx, file)

    opts = output.get_options(ctx)
    human_mode = not opts.json and not opts.quiet

    if human_mode:
        console.print(f"\n[bold blue]WordPress Bulk Editor - Sync[/bold blue]\n")
        console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    _validate_test_mode(test_mode, sheets)

    domain, username, password = load_and_print_credentials(
        env_file=env_file, domain=domain, username=username, password=password, ctx=ctx,
    )

    sync_config = _build_sync_config(
        dry_run=dry_run, concurrency=concurrency,
        skip_media=skip_media, skip_tax=skip_tax,
        no_update_excel=no_update_excel, delete_media=delete_media,
        skip_existing_media=skip_existing_media, columns=columns,
    )

    config = AppConfig.from_excel(
        excel_file=file, wp_url=domain, wp_username=username, wp_password=password,
        project_root=proj.root, sync=sync_config,
        logging=LoggingConfig(level="DEBUG" if (verbose or opts.verbose) else "INFO"),
    )

    if config.sync.columns_filter and human_mode:
        console.print(f"[yellow]--columns: обновляются только {', '.join(config.sync.columns_filter)}[/yellow]\n")

    config.paths.ensure_dirs()
    log_file = setup_logger(
        logs_folder=config.paths.logs_folder,
        config=config.logging,
        command_name="sync",
        console_stream=output.log_stream(ctx),
    )

    stats = asyncio.run(_run_sync_engine(config, sheets, test_mode=test_mode, command_name="sync"))

    def _render(payload: dict):
        console.print(f"\n[bold green]SUCCESS! Синхронизация завершена![/bold green]")
        console.print(f"Лог-файл: [cyan]{payload['log_file']}[/cyan]\n")

    output.emit_success(ctx, {**(stats or {}), "log_file": str(log_file)}, _render)


# ============================================================
# Async sync command
# ============================================================

@app.command(name="async")
def async_sync(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Путь к Excel файлу (авто-определяется из --project)",
    ),
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain (например, https://example.com)"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", help="WordPress Application Password"),
    sheets: Optional[str] = typer.Option(None, "--sheets", help="Список листов через запятую (posts,pages)"),
    env_file: Optional[Path] = typer.Option(
        None, "--env-file", help="Путь к .env файлу (по умолчанию .env)",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True,
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run (без изменений)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="Количество параллельных запросов (default: 5)"),
    api_timeout: Optional[int] = typer.Option(None, "--api-timeout", help="API timeout в секундах (default: 30)"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Максимум повторных попыток (default: 3)"),
    enable_circuit_breaker: bool = typer.Option(True, "--circuit-breaker/--no-circuit-breaker", help="Включить circuit breaker"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить загрузку медиа"),
    skip_tax: bool = typer.Option(False, "--skip-tax", help="Пропустить обновление таксономий (категории, теги)"),
    no_update_excel: bool = typer.Option(False, "--no-update-excel", help="Не записывать ID обратно в Excel"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять featured media при удалении записей (action=delete)"),
    skip_existing_media: bool = typer.Option(False, "--skip-existing-media", help="Не прикреплять существующие featured_media при update"),
    test_mode: bool = typer.Option(False, "--test-mode", help="Тестовый режим: обрабатывает только первые 3 строки"),
    columns: Optional[str] = typer.Option(None, "--columns", help="Колонки для обновления через запятую (excerpt,seo_title)"),
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
    file = require_excel_file(ctx, file)

    console.print(f"\n[bold cyan] WordPress Bulk Editor - Async Sync[/bold cyan]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    _validate_test_mode(test_mode, sheets)

    with handle_command_errors(ctx):
        domain, username, password = load_and_print_credentials(
            env_file=env_file, domain=domain, username=username, password=password,
        )

        concurrency_val, retry_cfg, circuit_breaker_obj = load_async_config_from_env(
            env_file=env_file, cli_concurrency=concurrency, cli_max_retries=max_retries,
        )
        if not enable_circuit_breaker:
            circuit_breaker_obj = None

        sync_config = _build_sync_config(
            dry_run=dry_run, concurrency=concurrency_val,
            skip_media=skip_media, skip_tax=skip_tax,
            no_update_excel=no_update_excel, delete_media=delete_media,
            skip_existing_media=skip_existing_media, columns=columns,
            timeout=api_timeout or 30,
        )

        config = AppConfig.from_excel(
            excel_file=file, wp_url=domain, wp_username=username, wp_password=password,
            project_root=proj.root, sync=sync_config,
            logging=LoggingConfig(level="DEBUG" if verbose else "INFO"),
        )

        if config.sync.columns_filter:
            console.print(f"[yellow]--columns: обновляются только {', '.join(config.sync.columns_filter)}[/yellow]\n")

        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="async_sync",
            console_stream=output.log_stream(ctx),
        )

        asyncio.run(_run_sync_engine(
            config, sheets, test_mode=test_mode,
            content_sync_cls=ContentSyncAsync,
            taxonomy_sync_cls=TaxonomySyncAsync,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj,
            command_name="async_sync",
        ))

        console.print(f"\n[bold green]SUCCESS! Асинхронная синхронизация завершена![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")


# ============================================================
# Public API для вызова из google_commands
# ============================================================

async def run_sync(config: AppConfig, sheets_filter: Optional[str] = None, test_mode: bool = False):
    """Обёртка _run_sync_engine для sync (используется google_commands)."""
    await _run_sync_engine(config, sheets_filter, test_mode=test_mode, command_name="sync")


async def run_sync_async(
    config: AppConfig,
    sheets_filter: Optional[str] = None,
    test_mode: bool = False,
    retry_config=None,
    circuit_breaker=None,
):
    """Обёртка _run_sync_engine для async (используется google_commands)."""
    await _run_sync_engine(
        config, sheets_filter, test_mode=test_mode,
        content_sync_cls=ContentSyncAsync,
        taxonomy_sync_cls=TaxonomySyncAsync,
        retry_config=retry_config,
        circuit_breaker=circuit_breaker,
        command_name="async_sync",
    )
