"""
CLI интерфейс для WordPress Bulk Editor
"""

from pathlib import Path
from typing import Optional, List, Dict
import asyncio
from datetime import datetime

import pandas as pd
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from loguru import logger

from .config import AppConfig, SyncConfig, PathsConfig, LoggingConfig, WordPressConfig
from .excel.reader import ExcelReader
from .excel.writer import ExcelWriter
from .excel.options import OptionsReader
from .wordpress.client import WordPressClient
from .wordpress.posts import PostsEndpoint
from .wordpress.taxonomies import TaxonomyCache, TaxonomiesEndpoint, normalize_taxonomy_name
from .wordpress.options import OptionsEndpoint
from .sync.content_sync import ContentSync
from .sync.media_sync import MediaSync
from .sync.taxonomy_sync import TaxonomySync
from .sync.content_sync_async import ContentSyncAsync
from .sync.taxonomy_sync_async import TaxonomySyncAsync
from .mappers.response_mapper import ResponseMapper
from .utils.logger import setup_logger, log_summary
from .utils.content_exporter import ContentExporter
from .utils.auth import load_credentials_from_env, load_async_config_from_env
from .exceptions import WPBulkEditorError

app = typer.Typer(
    name="wp-bulk-editor",
    help="WordPress Bulk Editor - синхронизация Excel ↔ WordPress"
)
console = Console()


@app.command()
def sync(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
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
    Синхронизация Excel → WordPress
    """
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

    try:
        # 1. Загружаем credentials (приоритет: CLI параметры → .env файл)
        console.print("[yellow]Загрузка credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )
        console.print(f"[green]OK[/green] WordPress: {domain}")
        console.print(f"[green]OK[/green] User: {username}\n")

        # 2. Создаем конфигурацию
        config = AppConfig.from_excel(
            excel_file=file,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
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

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


async def run_sync(config: AppConfig, sheets_filter: Optional[str] = None, test_mode: bool = False):
    """
    Основная логика синхронизации.

    Args:
        config: Конфигурация приложения
        sheets_filter: Фильтр листов (например "posts,pages")
        test_mode: Тестовый режим (обрабатывает только первые 3 строки)
    """
    started_at = datetime.now()

    # Статистика
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
        # 1. Открываем Excel файл
        with ExcelReader(config.paths.excel_file) as reader:
            sheets_by_type = reader.get_sheets_by_type()

            logger.info(f"Найдено листов:")
            logger.info(f"  Content: {len(sheets_by_type['content'])}")
            logger.info(f"  Taxonomy: {len(sheets_by_type['taxonomy'])}")

            # Фильтруем листы если указано
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

            # 2. Подключаемся к WordPress API
            async with WordPressClient(config.wordpress) as wp_client:
                # Проверка подключения
                await wp_client.test_connection()

                # 3. Загружаем кэш таксономий
                logger.info("Загрузка кэша таксономий...")
                taxonomy_cache = TaxonomyCache()

                # Определяем какие таксономии используются
                taxonomies_to_cache = set()
                for sheet_name in sheets_to_process:
                    sheet_type = reader.classify_sheet(sheet_name)
                    if sheet_type == "content":
                        df = reader.read_sheet(sheet_name)
                        # Ищем колонки таксономий
                        for col in df.columns:
                            if col in ["categories", "tags"] or col.endswith("-cat"):
                                taxonomies_to_cache.add(col)
                    elif sheet_type == "taxonomy":
                        taxonomies_to_cache.add(sheet_name)

                if taxonomies_to_cache:
                    await taxonomy_cache.load(wp_client, list(taxonomies_to_cache))

                # 4. Создаем ContentSync, MediaSync и TaxonomySync
                content_sync = ContentSync(wp_client, config, taxonomy_cache)
                media_sync = MediaSync(wp_client, config)
                taxonomy_sync = TaxonomySync(wp_client, config)

                # 5. Открываем Excel writer (если нужно)
                excel_writer = None
                if config.sync.update_excel:
                    excel_writer = ExcelWriter(
                        config.paths.excel_file,
                        taxonomy_cache=taxonomy_cache
                    )
                    excel_writer.load()

                # 6. Обработка каждого листа
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

                    # Обновляем общую статистику
                    stats["overall"]["total"] += sheet_stats.get("total", 0)
                    stats["overall"]["successful"] += (
                        sheet_stats.get("added", 0) +
                        sheet_stats.get("updated", 0) +
                        sheet_stats.get("deleted", 0)
                    )
                    stats["overall"]["errors"] += sheet_stats.get("errors", 0)

                # 7. Сохраняем Excel
                if excel_writer:
                    excel_writer.save()

        # Вычисляем success rate (от обработанных, без skipped)
        # Подсчитываем skipped из всех листов
        total_skipped = sum(
            sheet.get("skipped", 0) for sheet in stats["sheets"].values()
        )
        processed = stats["overall"]["total"] - total_skipped
        if processed > 0:
            stats["overall"]["success_rate"] = (
                stats["overall"]["successful"] / processed
            ) * 100
        elif stats["overall"]["total"] > 0:
            # Все строки skipped — 100% success rate
            stats["overall"]["success_rate"] = 100.0

        # Время завершения
        finished_at = datetime.now()
        stats["finished_at"] = finished_at.isoformat()
        stats["duration_seconds"] = (finished_at - started_at).total_seconds()

        # Выводим summary
        log_summary(stats, config.paths.logs_folder / "sync.log")

    except Exception as e:
        logger.exception("Ошибка синхронизации")
        raise


async def process_sheet(
    reader: ExcelReader,
    content_sync: ContentSync,
    media_sync,  # MediaSync
    taxonomy_sync: TaxonomySync,
    excel_writer: Optional[ExcelWriter],
    sheet_name: str,
    config: AppConfig,
    test_mode: bool = False
) -> dict:
    """
    Обработка одного листа.

    Args:
        test_mode: Тестовый режим (обрабатывает только первые 3 строки)

    Returns:
        Dict со статистикой
    """
    try:
        # Читаем лист
        df = reader.read_sheet(sheet_name)

        logger.info(f"Всего строк: {len(df)}")

        # В тестовом режиме берем только первые 3 строки
        if test_mode:
            original_len = len(df)
            df = df.head(3)
            logger.info(f"ТЕСТОВЫЙ РЕЖИМ: Обрабатываем только первые {len(df)} строк из {original_len}")

        # Проверяем есть ли колонка action
        if "action" not in df.columns:
            logger.warning(f"Лист {sheet_name} не содержит колонку 'action', пропускаем")
            return {
                "total": len(df),
                "added": 0,
                "updated": 0,
                "deleted": 0,
                "skipped": len(df),
                "errors": 0
            }

        # Синхронизируем через соответствующий sync
        sheet_type = reader.classify_sheet(sheet_name)

        if sheet_type == "content":
            # Синхронизация контента
            stats = await content_sync.sync_sheet(
                sheet_name,
                df,
                excel_writer
            )
            stats["total"] = len(df)
            return stats

        elif sheet_type == "media":
            # Синхронизация медиафайлов
            stats = await media_sync.sync_media(
                df,
                excel_writer
            )
            stats["total"] = len(df)
            return stats

        elif sheet_type == "taxonomy":
            # Синхронизация таксономий
            stats = await taxonomy_sync.sync_sheet(
                sheet_name,
                df,
                excel_writer
            )
            stats["total"] = len(df)
            return stats

        else:
            logger.warning(f"Неизвестный тип листа: {sheet_name}")
            return {
                "total": len(df),
                "added": 0,
                "updated": 0,
                "deleted": 0,
                "skipped": len(df),
                "errors": 0
            }

    except Exception as e:
        logger.error(f"Ошибка обработки листа {sheet_name}: {e}")
        logger.exception(e)
        return {
            "total": 0,
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "skipped": 0,
            "errors": 1
        }


@app.command()
def validate(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    )
):
    """
    Валидация Excel файла без синхронизации
    """
    console.print(f"\n[bold blue]Валидация файла[/bold blue]: {file}\n")

    try:
        # Проверка credentials
        console.print("[yellow]Проверка credentials из .env...[/yellow]")
        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] Domain: {domain}")
        console.print(f"[green]OK[/green] User: {username}")
        console.print(f"[green]OK[/green] Token: {'*' * 20}\n")

        # Проверка структуры
        console.print("[yellow]Проверка структуры листов...[/yellow]")
        with ExcelReader(file) as reader:
            sheets = reader.get_sheets_by_type()
            console.print(f"[green]OK[/green] Content sheets: {len(sheets['content'])}")
            console.print(f"[green]OK[/green] Taxonomy sheets: {len(sheets['taxonomy'])}\n")

        console.print("[bold green]SUCCESS! File is valid![/bold green]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)


@app.command()
def options(
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу",
        exists=False,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
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
    fetch: bool = typer.Option(
        False,
        "--fetch",
        help="Получить настройки из WordPress в Excel (вместо загрузки)"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Показать что будет отправлено, без отправки"
    )
):
    """
    Управление настройками сайта WordPress.

    Без --fetch: обновление WordPress из Excel
    С --fetch: выгрузка настроек из WordPress в Excel
    """
    console.print(f"\n[bold blue]WordPress Bulk Editor - Options[/bold blue]\n")

    # Настройка логирования
    logs_folder = Path.cwd() / "logs"
    logs_folder.mkdir(parents=True, exist_ok=True)

    from .config import LoggingConfig
    logging_config = LoggingConfig()

    command_name = "options_fetch" if fetch else "options"
    log_file = setup_logger(
        logs_folder=logs_folder,
        config=logging_config,
        command_name=command_name
    )
    logger.info(f"Лог-файл создан: {log_file}")
    logger.info(f"Режим: {'FETCH (выгрузка)' if fetch else 'UPDATE (загрузка)'}")

    # Режим FETCH - выгрузка из WordPress в Excel
    if fetch:
        # Определяем файл
        if file is None:
            file = Path.cwd() / "options.xlsx"
            console.print(f"[yellow]Auto mode:[/yellow] будет создан файл {file}\n")

            # Проверяем существование
            if file.exists():
                if not typer.confirm(f"Файл {file} уже существует. Перезаписать?"):
                    console.print("[red]Отменено пользователем[/red]")
                    raise typer.Exit(1)
        else:
            console.print(f"[yellow]Manual mode:[/yellow] используется файл {file}\n")

        console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

        try:
            # 1. Загружаем credentials
            console.print("[yellow]Загрузка credentials из .env...[/yellow]")
            domain, username, password = load_credentials_from_env(env_file=env_file)
            console.print(f"[green]OK[/green] WordPress: {domain}\n")

            # 2. Получаем настройки из WordPress
            console.print("[yellow]Получение настроек из WordPress...[/yellow]")

            wp_config = WordPressConfig(
                url=domain,
                username=username,
                password=password
            )

            site_options = asyncio.run(fetch_options(wp_config))

            if not site_options:
                console.print("[yellow]WARNING: Нет настроек для выгрузки[/yellow]\n")
                return

            console.print(f"[green]OK[/green] Получено настроек: {len(site_options)}\n")

            # Показываем что получено
            console.print("[bold]Полученные настройки:[/bold]")
            for key, value in site_options.items():
                console.print(f"  {key}: [cyan]{value}[/cyan]")
            console.print()

            # 3. Записываем в Excel
            console.print("[yellow]Запись настроек в Excel...[/yellow]")
            from .excel.options import OptionsWriter
            writer = OptionsWriter(file)
            writer.write(site_options, mode="replace")

            console.print(f"\n[bold green]SUCCESS! Настройки выгружены в {file}![/bold green]\n")

        except WPBulkEditorError as e:
            console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
            logger.exception("Неожиданная ошибка")
            raise typer.Exit(code=1)

    # Режим UPDATE - загрузка из Excel в WordPress
    else:
        if file is None:
            console.print("[red]ERROR: Параметр --file обязателен для загрузки настроек[/red]\n")
            raise typer.Exit(code=1)

        if not file.exists():
            console.print(f"[red]ERROR: Файл не найден: {file}[/red]\n")
            raise typer.Exit(code=1)

        console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

        try:
            # 1. Загружаем credentials из .env
            console.print("[yellow]Загрузка credentials из .env...[/yellow]")
            domain, username, password = load_credentials_from_env(env_file=env_file)
            console.print(f"[green]OK[/green] WordPress: {domain}\n")

            # 2. Читаем опции
            console.print("[yellow]Чтение настроек из листа 'options'...[/yellow]")
            from .excel.options import OptionsReader
            options_reader = OptionsReader(file)
            site_options = options_reader.read()

            if not site_options:
                console.print("[yellow]WARNING: Нет настроек для обновления[/yellow]\n")
                return

            console.print(f"[green]OK[/green] Найдено настроек: {len(site_options)}\n")

            # Показываем что будет отправлено
            console.print("[bold]Настройки для обновления:[/bold]")
            for key, value in site_options.items():
                console.print(f"  {key}: [cyan]{value}[/cyan]")
            console.print()

            if dry_run:
                console.print("[yellow]DRY-RUN режим: изменения не будут применены[/yellow]\n")
                console.print("[bold green]DRY-RUN завершен![/bold green]\n")
                return

            # 3. Подключаемся к WordPress и отправляем
            console.print("[yellow]Отправка настроек в WordPress...[/yellow]")

            wp_config = WordPressConfig(
                url=domain,
                username=username,
                password=password
            )

            asyncio.run(update_options(wp_config, site_options))

            console.print(f"\n[bold green]SUCCESS! Настройки обновлены![/bold green]\n")

        except WPBulkEditorError as e:
            console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
            logger.exception("Неожиданная ошибка")
            raise typer.Exit(code=1)


async def update_options(wp_config: WordPressConfig, site_options: dict):
    """
    Отправка настроек в WordPress.
    """
    async with WordPressClient(wp_config) as client:
        # Проверяем подключение
        await client.test_connection()

        # Отправляем настройки
        options_endpoint = OptionsEndpoint(client)
        await options_endpoint.update(site_options)


async def fetch_options(wp_config: WordPressConfig) -> dict:
    """
    Получение настроек из WordPress.

    Returns:
        Словарь с настройками {option_name: value}
    """
    async with WordPressClient(wp_config) as client:
        # Проверяем подключение
        await client.test_connection()

        # Получаем настройки
        options_endpoint = OptionsEndpoint(client)
        return await options_endpoint.get()


@app.command()
def fetch(
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу (если не указан, создается fetch.xlsx)",
        exists=False,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
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
    status: str = typer.Option(
        "any",
        "--status",
        help="Фильтр по статусу (publish, draft, any)"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Максимум записей на лист"
    ),
    exclude: Optional[str] = typer.Option(
        None,
        "--exclude",
        help="Список типов для исключения через запятую (posts,category)"
    )
):
    """
    Получение данных из WordPress → Excel

    Получает записи из WordPress и добавляет их в Excel файл
    после последней непустой строки каждого листа.
    """
    console.print(f"\n[bold blue]WordPress Bulk Editor - Fetch[/bold blue]\n")

    # Определяем режим работы
    if file is None:
        file = Path.cwd() / "fetch.xlsx"
        auto_mode = True
        console.print(f"[yellow]Auto mode:[/yellow] будет создан файл {file}\n")

        # Проверяем, не существует ли уже файл
        if file.exists():
            if not typer.confirm(f"Файл {file} уже существует. Перезаписать?"):
                console.print("[red]Отменено пользователем[/red]")
                raise typer.Exit(1)
    else:
        auto_mode = False
        console.print(f"[yellow]Manual mode:[/yellow] используется файл {file}\n")

    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    try:
        # 1. Настройка логирования
        logs_folder = Path.cwd() / "logs"
        logs_folder.mkdir(parents=True, exist_ok=True)

        from .config import LoggingConfig
        logging_config = LoggingConfig()

        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="fetch"
        )
        logger.info(f"Лог-файл создан: {log_file}")

        # 2. Загружаем credentials (приоритет: CLI параметры → .env файл)
        console.print("[yellow]Загрузка credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

        # 3. Определяем листы для обработки
        if auto_mode:
            # В auto-mode определяем листы через discovery
            if sheets:
                # Если указаны конкретные листы
                filter_list = [s.strip() for s in sheets.split(",")]
                sheets_to_fetch = filter_list
                console.print(f"[yellow]Листы для обработки:[/yellow] {', '.join(sheets_to_fetch)}\n")
            else:
                # Будут определены автоматически при discovery
                sheets_to_fetch = []
                console.print("[yellow]Листы будут определены автоматически[/yellow]\n")
        else:
            # Manual mode: читаем структуру из файла
            console.print("[yellow]Чтение структуры Excel файла...[/yellow]")
            with ExcelReader(file) as reader:
                sheets_by_type = reader.get_sheets_by_type()

                if sheets:
                    # Фильтруем листы если указано
                    filter_list = [s.strip() for s in sheets.split(",")]
                    sheets_to_fetch = filter_list
                else:
                    # Все content и taxonomy листы
                    sheets_to_fetch = sheets_by_type["content"] + sheets_by_type["taxonomy"]

                console.print(f"[green]OK[/green] Листы для обработки: {', '.join(sheets_to_fetch)}\n")

        # Применяем исключения если указаны
        if exclude:
            exclude_list = [s.strip() for s in exclude.split(",")]
            sheets_to_fetch = [s for s in sheets_to_fetch if s not in exclude_list]
            console.print(f"[yellow]Исключены:[/yellow] {', '.join(exclude_list)}\n")

        # 4. Подключаемся к WordPress
        console.print("[yellow]Подключение к WordPress...[/yellow]")

        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        asyncio.run(fetch_and_append(
            wp_config=wp_config,
            excel_file=file,
            sheets=sheets_to_fetch,
            status=status,
            limit=limit,
            auto_mode=auto_mode,
            exclude=exclude
        ))

        console.print(f"\n[bold green]SUCCESS! Данные добавлены в Excel![/bold green]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


async def fetch_and_append(
    wp_config: WordPressConfig,
    excel_file: Path,
    sheets: List[str],
    status: str,
    limit: Optional[int],
    auto_mode: bool = False,
    exclude: Optional[str] = None
):
    """
    Получить данные из WordPress и добавить в Excel.
    """
    async with WordPressClient(wp_config) as client:
        # Проверяем подключение
        await client.test_connection()

        if auto_mode:
            # === AUTO MODE: Создаём файл с нуля ===
            from .wordpress.discovery import WordPressDiscovery
            from .excel.creator import ExcelCreator
            # PostsEndpoint, TaxonomiesEndpoint, ResponseMapper уже импортированы глобально

            discovery = WordPressDiscovery(client)

            # 1. Обнаружение типов записей
            logger.info("Обнаружение типов записей...")
            all_post_types = await discovery.discover_post_types()

            # 2. Обнаружение таксономий
            logger.info("Обнаружение таксономий...")
            all_taxonomies = await discovery.discover_taxonomies()

            # 3. Если указан --sheets, фильтруем
            if sheets:
                all_post_types = [pt for pt in all_post_types if pt in sheets]
                all_taxonomies = [t for t in all_taxonomies if t in sheets]

            # 4. Если указан --exclude, исключаем
            if exclude:
                exclude_list = [s.strip() for s in exclude.split(",")]
                all_post_types = [pt for pt in all_post_types if pt not in exclude_list]
                all_taxonomies = [t for t in all_taxonomies if t not in exclude_list]
                logger.info(f"Исключены: {', '.join(exclude_list)}")

            # 5. Выгружаем данные и анализируем поля
            logger.info("Выгрузка данных для анализа полей...")
            sheets_data = {}  # {sheet_name: {"data": [...], "fields": [...], "type": "content/taxonomy"}}

            # Выгружаем записи
            for post_type in all_post_types:
                logger.info(f"Выгрузка данных для '{post_type}'...")
                posts_endpoint = PostsEndpoint(client, post_type)
                posts = await posts_endpoint.get_all(status=status)

                if posts:
                    # Анализируем поля из реальных данных
                    fields = await discovery.analyze_fields_from_data(posts, "content")
                    sheets_data[post_type] = {
                        "data": posts,
                        "fields": fields,
                        "type": "content"
                    }
                    logger.info(f"'{post_type}': выгружено {len(posts)} записей, {len(fields)} полей")
                else:
                    logger.warning(f"'{post_type}': нет данных, лист не будет создан")

            # Выгружаем таксономии
            for taxonomy in all_taxonomies:
                logger.info(f"Выгрузка данных для таксономии '{taxonomy}'...")
                # Нормализуем имя таксономии для API
                normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                terms = await tax_endpoint.get_all()

                if terms:
                    # Анализируем поля из реальных данных
                    fields = await discovery.analyze_fields_from_data(terms, "taxonomy")
                    sheets_data[taxonomy] = {
                        "data": terms,
                        "fields": fields,
                        "type": "taxonomy"
                    }
                    logger.info(f"'{taxonomy}': выгружено {len(terms)} терминов, {len(fields)} полей")
                else:
                    logger.warning(f"'{taxonomy}': нет данных, лист не будет создан")

            # 6. Создаём Excel файл на основе реальных полей
            logger.info(f"Создание Excel файла {excel_file}...")
            sheets_fields = {name: meta["fields"] for name, meta in sheets_data.items()}
            creator = ExcelCreator()
            creator.create_file_with_fields(excel_file, sheets_fields)

            # 7. Записываем данные сразу (без повторной выгрузки)
            logger.info("Запись данных в Excel...")
            # ExcelWriter уже импортирован глобально
            excel_writer = ExcelWriter(excel_file)
            excel_writer.load()

            # Загружаем taxonomy cache для преобразования ID → названия
            logger.info("Загрузка кэша таксономий для преобразования ID в названия...")
            taxonomy_cache = {}

            # Собираем список всех таксономий: стандартные + кастомные
            all_taxonomies_for_cache = set()

            # Добавляем стандартные таксономии (categories, tags)
            all_taxonomies_for_cache.add("categories")
            all_taxonomies_for_cache.add("tags")

            # Добавляем таксономии из sheets_data (те что type=="taxonomy")
            for sheet_name, sheet_meta in sheets_data.items():
                if sheet_meta["type"] == "taxonomy":
                    all_taxonomies_for_cache.add(sheet_name)

            # Загружаем термины для каждой таксономии
            for taxonomy in all_taxonomies_for_cache:
                try:
                    normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                    tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                    terms = await tax_endpoint.get_all()

                    # Формируем словарь {term_id: term_name}
                    taxonomy_cache[taxonomy] = {
                        term["id"]: term["name"]
                        for term in terms
                    }
                    logger.debug(f"Загружено {len(terms)} терминов для таксономии '{taxonomy}'")
                except Exception as e:
                    logger.warning(f"Не удалось загрузить термины для '{taxonomy}': {e}")
                    taxonomy_cache[taxonomy] = {}

            logger.info(f"Кэш таксономий загружен: {len(taxonomy_cache)} таксономий")

            # Подготавливаем mapper с заполненным taxonomy cache
            mapper = ResponseMapper(taxonomy_cache=taxonomy_cache)

            for sheet_name, sheet_meta in sheets_data.items():
                logger.info(f"Запись данных в лист '{sheet_name}'...")

                if sheet_meta["type"] == "content":
                    # Преобразуем записи в формат Excel
                    rows = [mapper.map_post_to_excel(post) for post in sheet_meta["data"]]
                else:
                    # Преобразуем термины в формат Excel
                    rows = [mapper.map_term_to_excel(term) for term in sheet_meta["data"]]

                # Записываем в Excel
                excel_writer.replace_rows(sheet_name, rows)

                if limit:
                    rows_limited = rows[:limit]
                    excel_writer.replace_rows(sheet_name, rows_limited)
                    logger.info(f"Записано {len(rows_limited)} строк (лимит: {limit})")
                else:
                    excel_writer.replace_rows(sheet_name, rows)
                    logger.info(f"Записано {len(rows)} строк")

            excel_writer.save()
            logger.info("✓ Данные успешно записаны в Excel")

            # Завершаем функцию, т.к. данные уже записаны
            return

        # Загружаем taxonomy cache для преобразования ID → названия
        logger.info("Загрузка кэша таксономий...")
        # TaxonomiesEndpoint уже импортирован глобально
        from .mappers.field_detector import FieldDetector

        # Определяем все таксономии из листов для обработки
        taxonomies_to_load = set(["categories", "tags"])  # Стандартные

        # Добавляем кастомные таксономии из колонок листов
        excel_reader = ExcelReader(excel_file)
        for sheet_name in sheets:
            try:
                df = excel_reader.read_sheet(sheet_name)
                detector = FieldDetector(df.columns.tolist())
                taxonomy_fields = detector.get_taxonomy_fields()

                # Добавляем найденные таксономии (кроме стандартных categories/tags)
                for tax_field in taxonomy_fields:
                    if tax_field not in ["categories", "tags"]:
                        taxonomies_to_load.add(tax_field)

            except Exception as e:
                logger.warning(f"Не удалось прочитать структуру листа '{sheet_name}': {e}")

        logger.info(f"Найдено таксономий для загрузки: {taxonomies_to_load}")

        taxonomy_cache_data = {}
        for taxonomy in taxonomies_to_load:
            try:
                normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                terms = await tax_endpoint.get_all()
                # Создаем словарь {term_id: term_name}
                taxonomy_cache_data[taxonomy] = {
                    term["id"]: term["name"] for term in terms
                }
                logger.info(f"Загружено {len(terms)} терминов для '{taxonomy}'")
            except Exception as e:
                logger.warning(f"Не удалось загрузить таксономию '{taxonomy}': {e}")
                taxonomy_cache_data[taxonomy] = {}

        # Создаем mapper с taxonomy cache
        mapper = ResponseMapper(taxonomy_cache=taxonomy_cache_data)

        # Открываем Excel writer
        excel_writer = ExcelWriter(excel_file)
        excel_writer.load()

        # Обрабатываем каждый лист
        for sheet_name in sheets:
            logger.info(f"\n{'='*50}")
            logger.info(f"Получение данных для листа: {sheet_name}")
            logger.info(f"{'='*50}")

            # Определяем тип листа (таксономия или контент)
            is_taxonomy = (
                sheet_name in ["category", "tag", "categories", "tags"] or
                sheet_name.endswith("-cat")
            )

            if is_taxonomy:
                # Обрабатываем как таксономию
                # TaxonomiesEndpoint уже импортирован глобально

                # Нормализуем название таксономии для API endpoint
                taxonomy_name = normalize_taxonomy_name(sheet_name)

                tax_endpoint = TaxonomiesEndpoint(client, taxonomy_name)

                logger.info(f"Получение терминов таксономии '{taxonomy_name}'...")
                terms = await tax_endpoint.get_all()

                if limit:
                    terms = terms[:limit]

                logger.info(f"Получено {len(terms)} терминов")

                if not terms:
                    logger.warning(f"Нет терминов для добавления в {sheet_name}")
                    continue

                # Преобразуем в формат Excel
                rows = []
                for term in terms:
                    row = mapper.map_term_to_excel(term)
                    rows.append(row)

            else:
                # Обрабатываем как контент (posts, pages, custom types)
                posts_endpoint = PostsEndpoint(client, sheet_name)

                logger.info(f"Получение записей из WordPress (status={status})...")
                posts = await posts_endpoint.get_all(status=status)

                if limit:
                    posts = posts[:limit]

                logger.info(f"Получено {len(posts)} записей")

                if not posts:
                    logger.warning(f"Нет записей для добавления в {sheet_name}")
                    continue

                # Преобразуем в формат Excel
                rows = []
                for post in posts:
                    row = mapper.map_post_to_excel(post)
                    rows.append(row)

            # Заменяем все строки в Excel (кроме заголовков)
            excel_writer.replace_rows(sheet_name, rows)
            logger.info(f"Заменено {len(rows)} строк в листе '{sheet_name}'")

        # Сохраняем файл
        excel_writer.save()
        logger.info("Excel файл сохранен")


@app.command()
def export(
    domain: Optional[str] = typer.Option(
        None,
        "--domain",
        "-d",
        help="WordPress domain (например, https://example.com)"
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        "-u",
        help="WordPress username"
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        "-p",
        help="WordPress Application Password"
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
    output: Path = typer.Option(
        Path("exported_content"),
        "--output",
        "-o",
        help="Папка для экспорта контента"
    ),
    concurrency: int = typer.Option(
        10,
        "--concurrency",
        "-c",
        help="Количество параллельных загрузок (по умолчанию 10)"
    ),
    include_media: bool = typer.Option(
        False,
        "--include-media",
        help="Включить экспорт метаданных медиафайлов (без скачивания файлов)"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    )
):
    """
    Экспорт всего контента сайта в файлы.

    Находит все типы контента (posts, pages, custom types) и экспортирует их
    в отдельные HTML файлы, организованные по папкам. Создает CSV индекс.

    Примеры:
        # Экспорт с явным указанием credentials
        python -m src.cli export --domain https://example.com --username user --password "pass word"

        # Экспорт используя credentials из .env
        python -m src.cli export --output my_export

        # Экспорт используя кастомный .env файл
        python -m src.cli export --env-file project2/.env --output my_export

        # Экспорт с метаданными медиа (без скачивания файлов)
        python -m src.cli export --include-media
    """
    try:
        # Настройка логирования
        logs_folder = Path("logs")
        logs_folder.mkdir(parents=True, exist_ok=True)

        from .config import LoggingConfig
        logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="export"
        )

        console.print("\n[bold cyan]WordPress Content Exporter[/bold cyan]")
        console.print("=" * 60 + "\n")

        # Получаем credentials (приоритет: CLI параметры → .env файл)
        console.print(f"[yellow]→[/yellow] Загрузка credentials...")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )

        console.print(f"[green]✓[/green] WordPress: {domain}")
        if username:
            console.print(f"[yellow]→[/yellow] Пользователь: {username}")
        else:
            console.print(f"[yellow]→[/yellow] Режим: публичный доступ (без авторизации)")
        console.print(f"[yellow]→[/yellow] Папка экспорта: {output}")
        console.print(f"[yellow]→[/yellow] Параллельных загрузок: {concurrency}")
        if include_media:
            console.print(f"[yellow]→[/yellow] Экспорт медиа: Да (только метаданные)")
        console.print()

        # Создаем WordPress клиент
        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        # Запускаем экспорт
        stats = asyncio.run(export_content(wp_config, output, concurrency, include_media))

        # Показываем результаты
        console.print("\n[bold green]Экспорт завершен![/bold green]")
        console.print("=" * 60)

        total = 0
        for post_type, count in stats.items():
            console.print(f"  {post_type}: [cyan]{count}[/cyan] записей")
            total += count

        console.print(f"\n[bold]Всего экспортировано:[/bold] [green]{total}[/green] записей")
        console.print(f"[bold]CSV индекс:[/bold] {output / 'index.csv'}")
        console.print(f"\n[dim]Логи: {log_file}[/dim]")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]✗ Ошибка:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[bold red]✗ Неожиданная ошибка:[/bold red] {e}")
        logger.exception("Критическая ошибка")
        raise typer.Exit(1)


async def export_content(
    wp_config: WordPressConfig,
    output_folder: Path,
    concurrency: int = 10,
    include_media: bool = False
) -> Dict[str, int]:
    """
    Основная функция экспорта контента.

    Args:
        wp_config: Конфигурация WordPress
        output_folder: Папка для экспорта
        concurrency: Количество параллельных загрузок
        include_media: Включить экспорт метаданных медиа

    Returns:
        Статистика по типам контента
    """
    async with WordPressClient(wp_config) as client:
        # Создаем exporter
        exporter = ContentExporter(client, output_folder, concurrency)

        # Экспортируем весь контент
        logger.info("Начало экспорта контента...")
        stats = await exporter.export_all(include_media=include_media)
        logger.info("Экспорт завершен")

        return stats


@app.command()
def fetch_media(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
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
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
):
    """
    Загрузить список медиафайлов из WordPress в Excel (лист media)
    """
    console.print(f"\n[bold blue]WordPress Bulk Editor - Fetch Media[/bold blue]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    try:
        # 1. Загружаем credentials из .env
        console.print("[yellow]Загрузка credentials из .env...[/yellow]")
        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

        # 2. Создаем конфигурацию
        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")

        # 3. Настройка логирования
        logs_folder = Path("logs")
        logs_folder.mkdir(parents=True, exist_ok=True)
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="fetch_media"
        )

        # 4. Запускаем fetch
        asyncio.run(run_fetch_media(wp_config, file))

        console.print(f"\n[bold green]SUCCESS! Медиафайлы загружены в лист 'media'![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


async def run_fetch_media(wp_config: WordPressConfig, excel_file: Path):
    """
    Основная логика fetch media.

    Args:
        wp_config: Конфигурация WordPress
        excel_file: Путь к Excel файлу
    """
    from .wordpress.media import MediaEndpoint

    async with WordPressClient(wp_config) as client:
        # Создаем endpoint
        media_endpoint = MediaEndpoint(client)

        # Получаем все медиафайлы
        logger.info("Fetching media from WordPress...")
        all_media = await media_endpoint.fetch_all_media()

        if not all_media:
            logger.info("No media files found")
            console.print("[yellow]Медиафайлы не найдены[/yellow]")
            return

        # Преобразуем в DataFrame
        media_rows = []
        for media in all_media:
            # Извлекаем заголовок
            title = media.get("title", {})
            if isinstance(title, dict):
                title_text = title.get("rendered", "")
            else:
                title_text = str(title)

            # Извлекаем caption и description
            caption = media.get("caption", {})
            if isinstance(caption, dict):
                caption_text = caption.get("rendered", "")
            else:
                caption_text = str(caption) if caption else ""

            description = media.get("description", {})
            if isinstance(description, dict):
                description_text = description.get("rendered", "")
            else:
                description_text = str(description) if description else ""

            media_rows.append({
                "action": "skip",  # По умолчанию skip
                "img_path": "",
                "id": media.get("id", ""),
                "date": media.get("date", ""),
                "modified": media.get("modified", ""),
                "title": title_text,
                "type": media.get("type", ""),
                "slug": media.get("slug", ""),
                "link": media.get("link", ""),
                "status": media.get("status", ""),
                "guid": media.get("guid", {}).get("rendered", ""),
                "caption": caption_text,
                "alt_text": media.get("alt_text", ""),
                "media_type": media.get("media_type", ""),
                "mime_type": media.get("mime_type", ""),
                "post": media.get("post", ""),
                "source_url": media.get("source_url", ""),
            })

        df = pd.DataFrame(media_rows)

        # Записываем в Excel
        logger.info(f"Writing {len(df)} media records to Excel...")
        with pd.ExcelWriter(excel_file, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name="media", index=False)

        logger.info(f"Successfully written {len(df)} media records to sheet 'media'")
        console.print(f"[green]Загружено:[/green] {len(df)} медиафайлов")


@app.command()
def export_media(
    domain: Optional[str] = typer.Option(
        None,
        "--domain",
        help="URL WordPress сайта (например, https://example.com)"
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        help="Имя пользователя WordPress"
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        help="Application Password"
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
    output: Path = typer.Option(
        Path("export_media"),
        "--output",
        "-o",
        help="Папка для экспорта"
    ),
    download_files: bool = typer.Option(
        True,
        "--download-files/--no-download-files",
        help="Скачивать файлы или только метаданные"
    ),
    concurrency: int = typer.Option(
        10,
        "--concurrency",
        help="Количество параллельных загрузок"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
):
    """
    Экспортировать медиафайлы из WordPress в CSV (с файлами или без)
    """
    console.print(f"\n[bold blue]WordPress Bulk Editor - Export Media[/bold blue]\n")
    console.print(f"Папка экспорта: [cyan]{output}[/cyan]\n")

    try:
        # 1. Получаем credentials (приоритет: CLI параметры → .env файл)
        console.print("[yellow]Загрузка credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )

        # 2. Создаем конфигурацию
        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        logging_config = LoggingConfig(level=("DEBUG" if verbose else "INFO"))

        # 3. Настройка логирования
        logs_folder = Path("logs")
        logs_folder.mkdir(parents=True, exist_ok=True)
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="export_media"
        )

        console.print(f"[green]WordPress:[/green] {domain}")
        console.print(f"[green]Скачивание файлов:[/green] {'Да' if download_files else 'Нет'}")
        console.print(f"[green]Параллельность:[/green] {concurrency}\n")

        # 4. Запускаем экспорт
        count = asyncio.run(run_export_media(wp_config, output, download_files, concurrency))

        console.print(f"\n[bold green]SUCCESS! Экспорт завершен![/bold green]")
        console.print(f"Экспортировано: [cyan]{count}[/cyan] медиафайлов")
        console.print(f"CSV индекс: [cyan]{output / 'media_index.csv'}[/cyan]")
        console.print(f"\n[dim]Логи: {log_file}[/dim]")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]✗ Ошибка:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[bold red]✗ Неожиданная ошибка:[/bold red] {e}")
        logger.exception("Критическая ошибка")
        raise typer.Exit(1)


async def run_export_media(
    wp_config: WordPressConfig,
    output_folder: Path,
    download_files: bool,
    concurrency: int
) -> int:
    """
    Основная функция экспорта медиа.

    Args:
        wp_config: Конфигурация WordPress
        output_folder: Папка для экспорта
        download_files: Скачивать ли файлы
        concurrency: Количество параллельных загрузок

    Returns:
        Количество экспортированных медиафайлов
    """
    from .wordpress.media import MediaEndpoint
    from .utils.media_exporter import MediaExporter

    async with WordPressClient(wp_config) as client:
        # Создаем endpoint и exporter
        media_endpoint = MediaEndpoint(client)
        media_exporter = MediaExporter(
            media_endpoint,
            output_folder,
            download_files=download_files,
            concurrency=concurrency
        )

        # Экспортируем
        logger.info("Начало экспорта медиафайлов...")
        count = await media_exporter.export_all_media()
        logger.info("Экспорт завершен")

        return count


@app.command()
def download_google_sheet(
    sheet_id: Optional[str] = typer.Option(
        None,
        "--sheet-id",
        help="ID Google Sheets документа"
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Путь к JSON файлу с credentials"
    ),
    output: Path = typer.Option(
        Path("export.xlsx"),
        "--output",
        "-o",
        help="Путь для сохранения .xlsx файла (по умолчанию export.xlsx)"
    ),
):
    """
    Скачать Google Sheets документ в формате Excel (.xlsx)

    Данные берутся из переменных окружения .env:
    - GOOGLE_SHEET_ID - ID таблицы
    - GOOGLE_SHEET_CREDENTIALS_PATH - путь к credentials.json

    Примеры:
        # Скачать используя переменные окружения
        python -m src.cli download-google-sheet

        # Скачать с явным указанием параметров
        python -m src.cli download-google-sheet --sheet-id "1B5gj1n..." --credentials "credentials/google.json" --output "my_file.xlsx"
    """
    console.print("\n[bold cyan]Google Sheets Downloader[/bold cyan]")
    console.print("=" * 60 + "\n")

    try:
        import os
        from dotenv import load_dotenv
        from .utils.google_sheets import GoogleSheetsClient

        # Загружаем .env
        load_dotenv()

        # Получаем параметры (приоритет: CLI > .env)
        final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

        # Валидация
        if not final_sheet_id:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_ID (через --sheet-id или .env)")
            raise typer.Exit(1)

        if not final_credentials:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_CREDENTIALS_PATH (через --credentials или .env)")
            raise typer.Exit(1)

        credentials_path = Path(final_credentials)
        if not credentials_path.exists():
            console.print(f"[red]✗[/red] Файл credentials не найден: {credentials_path}")
            raise typer.Exit(1)

        console.print(f"[yellow]→[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]→[/yellow] Credentials: {credentials_path}")
        console.print(f"[yellow]→[/yellow] Файл назначения: {output}")
        console.print()

        # Скачиваем
        console.print("[yellow]Скачивание Google Sheets...[/yellow]")

        downloader = GoogleSheetsClient(credentials_path)

        # Получаем информацию о документе
        info = downloader.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]✓[/green] Документ: {info.get('name', 'Unknown')}")
        console.print(f"[green]✓[/green] Последнее изменение: {info.get('modified_time', 'Unknown')}")
        console.print()

        # Скачиваем файл
        downloader.download_as_excel(final_sheet_id, output)

        console.print(f"\n[bold green]✓ Файл успешно скачан![/bold green]")
        console.print(f"[bold]Сохранено:[/bold] {output}")

    except Exception as e:
        console.print(f"\n[bold red]✗ Ошибка:[/bold red] {e}")
        logger.exception("Критическая ошибка")
        raise typer.Exit(1)


@app.command()
def upload_to_google_sheets(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу для загрузки",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    sheet_id: Optional[str] = typer.Option(
        None,
        "--sheet-id",
        help="ID Google Sheets документа"
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Путь к JSON файлу с credentials"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов для обновления через запятую (posts,pages). Если не указано - обновляются все листы"
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
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    )
):
    """
    Загрузить локальный Excel файл в Google Sheets

    Обновляет указанные листы (или все листы) в Google Sheets документе
    данными из локального Excel файла.

    Данные берутся из переменных окружения .env:
    - GOOGLE_SHEET_ID - ID таблицы
    - GOOGLE_SHEET_CREDENTIALS_PATH - путь к credentials.json

    Примеры:
        # Загрузить весь файл (используя .env)
        python -m src.cli upload-to-google-sheets --file export.xlsx

        # Загрузить только определенные листы
        python -m src.cli upload-to-google-sheets --file export.xlsx --sheets posts,pages

        # С явным указанием параметров
        python -m src.cli upload-to-google-sheets --file data.xlsx --sheet-id "1B5gj1n..." --credentials "credentials/google.json"
    """
    console.print("\n[bold cyan]Upload Excel → Google Sheets[/bold cyan]")
    console.print("=" * 60 + "\n")

    try:
        import os
        from dotenv import load_dotenv
        from .utils.google_sheets import GoogleSheetsClient

        # Настройка логирования
        if verbose:
            from .config import LoggingConfig
            logs_folder = Path("logs")
            logs_folder.mkdir(parents=True, exist_ok=True)
            logging_config = LoggingConfig(level="DEBUG")
            setup_logger(
                logs_folder=logs_folder,
                config=logging_config,
                command_name="upload_to_google_sheets"
            )

        # Загружаем .env
        load_dotenv()

        # Получаем параметры (приоритет: CLI > .env)
        final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

        # Валидация
        if not final_sheet_id:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_ID (через --sheet-id или .env)")
            raise typer.Exit(1)

        if not final_credentials:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_CREDENTIALS_PATH (через --credentials или .env)")
            raise typer.Exit(1)

        credentials_path = Path(final_credentials)
        if not credentials_path.exists():
            console.print(f"[red]✗[/red] Файл credentials не найден: {credentials_path}")
            raise typer.Exit(1)

        console.print(f"[yellow]→[/yellow] Excel файл: {file}")
        console.print(f"[yellow]→[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]→[/yellow] Credentials: {credentials_path}")

        # Определяем листы для загрузки
        sheets_to_upload = None
        if sheets:
            sheets_to_upload = [s.strip() for s in sheets.split(",")]
            console.print(f"[yellow]→[/yellow] Листы для обновления: {', '.join(sheets_to_upload)}")
        else:
            console.print(f"[yellow]→[/yellow] Будут обновлены все листы из Excel файла")

        console.print()

        # Создаем клиент
        console.print("[yellow]Загрузка в Google Sheets...[/yellow]")
        gs_client = GoogleSheetsClient(credentials_path)

        # Загружаем
        gs_client.upload_excel_to_sheets(
            excel_path=file,
            spreadsheet_id=final_sheet_id,
            sheets_to_update=sheets_to_upload
        )

        console.print(f"\n[bold green]✓ Данные успешно загружены в Google Sheets![/bold green]")
        console.print(f"[bold]Локальный файл:[/bold] {file}\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


@app.command()
def sync_from_google(
    sheet_id: Optional[str] = typer.Option(
        None,
        "--sheet-id",
        help="ID Google Sheets документа"
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Путь к JSON файлу с credentials"
    ),
    output: Path = typer.Option(
        Path("export.xlsx"),
        "--output",
        "-o",
        help="Путь для сохранения .xlsx файла (по умолчанию export.xlsx)"
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
):
    """
    Скачать Google Sheets и синхронизировать с WordPress (пайплайн)

    Эта команда объединяет download-google-sheet и sync в один пайплайн:
    1. Скачивает Google Sheets документ в формате Excel
    2. Выполняет синхронизацию с WordPress

    Данные берутся из переменных окружения .env:
    - GOOGLE_SHEET_ID - ID таблицы
    - GOOGLE_SHEET_CREDENTIALS_PATH - путь к credentials.json

    Примеры:
        # Скачать и синхронизировать используя .env
        python -m src.cli sync-from-google

        # С явным указанием параметров
        python -m src.cli sync-from-google --sheet-id "1B5gj1n..." --credentials "credentials/google.json" --output "data.xlsx"

        # С фильтром листов
        python -m src.cli sync-from-google --sheets posts,pages
    """
    console.print("\n[bold cyan]Google Sheets → WordPress Pipeline[/bold cyan]")
    console.print("=" * 60 + "\n")

    try:
        import os
        from dotenv import load_dotenv
        from .utils.google_sheets import GoogleSheetsClient

        # ========== ШАГ 1: СКАЧИВАНИЕ GOOGLE SHEETS ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        # Загружаем .env
        load_dotenv()

        # Получаем параметры (приоритет: CLI > .env)
        final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

        # Валидация
        if not final_sheet_id:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_ID (через --sheet-id или .env)")
            raise typer.Exit(1)

        if not final_credentials:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_CREDENTIALS_PATH (через --credentials или .env)")
            raise typer.Exit(1)

        credentials_path = Path(final_credentials)
        if not credentials_path.exists():
            console.print(f"[red]✗[/red] Файл credentials не найден: {credentials_path}")
            raise typer.Exit(1)

        console.print(f"[yellow]→[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]→[/yellow] Credentials: {credentials_path}")
        console.print(f"[yellow]→[/yellow] Файл назначения: {output}")
        console.print()

        # Скачиваем
        console.print("[yellow]Скачивание Google Sheets...[/yellow]")

        downloader = GoogleSheetsClient(credentials_path)

        # Получаем информацию о документе
        info = downloader.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]✓[/green] Документ: {info.get('name', 'Unknown')}")
        console.print(f"[green]✓[/green] Последнее изменение: {info.get('modified_time', 'Unknown')}")
        console.print()

        # Скачиваем файл
        downloader.download_as_excel(final_sheet_id, output)

        console.print(f"[bold green]✓ Файл успешно скачан![/bold green]")
        console.print(f"[bold]Сохранено:[/bold] {output}\n")

        # ========== ШАГ 2: СИНХРОНИЗАЦИЯ С WORDPRESS ==========
        console.print("[bold yellow]ШАГ 2: Синхронизация с WordPress[/bold yellow]\n")

        # Загружаем credentials из .env
        console.print("[yellow]Загрузка credentials из .env...[/yellow]")
        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] WordPress: {domain}")
        console.print(f"[green]OK[/green] User: {username}\n")

        # Создаем конфигурацию для sync
        config = AppConfig.from_excel(
            excel_file=output,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
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

        # Настройка логирования
        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="sync_from_google"
        )

        # Запускаем синхронизацию
        asyncio.run(run_sync(config, sheets))

        console.print(f"\n[bold green]SUCCESS! Пайплайн завершен![/bold green]")
        console.print(f"Скачанный файл: [cyan]{output}[/cyan]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


@app.command()
def sync_google_bidirectional(
    sheet_id: Optional[str] = typer.Option(
        None,
        "--sheet-id",
        help="ID Google Sheets документа"
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Путь к JSON файлу с credentials"
    ),
    output: Path = typer.Option(
        Path("export.xlsx"),
        "--output",
        "-o",
        help="Путь для сохранения .xlsx файла (по умолчанию export.xlsx)"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов для обновления через запятую (posts,pages). Если не указано - обновляются все листы"
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
    no_upload_back: bool = typer.Option(
        False,
        "--no-upload-back",
        help="Не загружать обратно в Google Sheets после синхронизации"
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
):
    """
    Двусторонняя синхронизация: Google Sheets ↔ WordPress ↔ Google Sheets

    Полный пайплайн работы с Google Sheets:
    1. Скачивает Google Sheets документ в формате Excel
    2. Выполняет синхронизацию с WordPress
    3. Загружает обновленный файл обратно в Google Sheets

    Данные берутся из переменных окружения .env:
    - GOOGLE_SHEET_ID - ID таблицы
    - GOOGLE_SHEET_CREDENTIALS_PATH - путь к credentials.json
    - WORDPRESS_URL, WORDPRESS_USERNAME, WORDPRESS_PASSWORD

    Примеры:
        # Полный цикл используя .env
        python -m src.cli sync-google-bidirectional

        # С явным указанием параметров и фильтром листов
        python -m src.cli sync-google-bidirectional --sheet-id "1B5gj1n..." --sheets posts,pages

        # Без загрузки обратно (только скачать и синхронизировать)
        python -m src.cli sync-google-bidirectional --no-upload-back
    """
    console.print("\n[bold cyan]Google Sheets ↔ WordPress ↔ Google Sheets Pipeline[/bold cyan]")
    console.print("=" * 60 + "\n")

    try:
        import os
        from dotenv import load_dotenv
        from .utils.google_sheets import GoogleSheetsClient

        # ========== ШАГ 1: СКАЧИВАНИЕ GOOGLE SHEETS ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        # Загружаем .env
        load_dotenv()

        # Получаем параметры (приоритет: CLI > .env)
        final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

        # Валидация
        if not final_sheet_id:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_ID (через --sheet-id или .env)")
            raise typer.Exit(1)

        if not final_credentials:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_CREDENTIALS_PATH (через --credentials или .env)")
            raise typer.Exit(1)

        credentials_path = Path(final_credentials)
        if not credentials_path.exists():
            console.print(f"[red]✗[/red] Файл credentials не найден: {credentials_path}")
            raise typer.Exit(1)

        console.print(f"[yellow]→[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]→[/yellow] Credentials: {credentials_path}")
        console.print(f"[yellow]→[/yellow] Файл назначения: {output}")
        console.print()

        # Создаем клиент
        console.print("[yellow]Скачивание Google Sheets...[/yellow]")
        gs_client = GoogleSheetsClient(credentials_path)

        # Получаем информацию о документе
        info = gs_client.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]✓[/green] Документ: {info.get('name', 'Unknown')}")
        console.print(f"[green]✓[/green] Последнее изменение: {info.get('modified_time', 'Unknown')}")
        console.print()

        # Скачиваем файл
        gs_client.download_as_excel(final_sheet_id, output)

        console.print(f"[bold green]✓ Файл успешно скачан![/bold green]")
        console.print(f"[bold]Сохранено:[/bold] {output}\n")

        # ========== ШАГ 2: СИНХРОНИЗАЦИЯ С WORDPRESS ==========
        console.print("[bold yellow]ШАГ 2: Синхронизация с WordPress[/bold yellow]\n")

        # Загружаем credentials из .env
        console.print("[yellow]Загрузка WordPress credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] WordPress: {domain}")
        console.print(f"[green]OK[/green] User: {username}\n")

        # Создаем конфигурацию для sync
        config = AppConfig.from_excel(
            excel_file=output,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
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

        # Настройка логирования
        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="sync_google_bidirectional"
        )

        # Запускаем синхронизацию
        asyncio.run(run_sync(config, sheets))

        console.print(f"\n[bold green]✓ Синхронизация с WordPress завершена![/bold green]\n")

        # ========== ШАГ 3: ЗАГРУЗКА ОБРАТНО В GOOGLE SHEETS ==========
        if not no_upload_back and not dry_run:
            console.print("[bold yellow]ШАГ 3: Загрузка обратно в Google Sheets[/bold yellow]\n")

            # Определяем листы для загрузки
            sheets_to_upload = None
            if sheets:
                sheets_to_upload = [s.strip() for s in sheets.split(",")]
                console.print(f"[yellow]→[/yellow] Листы для обновления: {', '.join(sheets_to_upload)}")
            else:
                console.print(f"[yellow]→[/yellow] Будут обновлены все листы из Excel файла")

            console.print()
            console.print("[yellow]Загрузка в Google Sheets...[/yellow]")

            # Загружаем обратно
            gs_client.upload_excel_to_sheets(
                excel_path=output,
                spreadsheet_id=final_sheet_id,
                sheets_to_update=sheets_to_upload
            )

            console.print(f"[bold green]✓ Данные успешно загружены в Google Sheets![/bold green]\n")
        elif dry_run:
            console.print("[yellow]ШАГ 3 пропущен: DRY-RUN режим[/yellow]\n")
        else:
            console.print("[yellow]ШАГ 3 пропущен: --no-upload-back флаг[/yellow]\n")

        # ========== ИТОГИ ==========
        console.print(f"[bold green]SUCCESS! Полный пайплайн завершен![/bold green]")
        console.print(f"Локальный файл: [cyan]{output}[/cyan]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


@app.command()
def bulk_delete(
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
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    post_type: str = typer.Option(
        ...,
        "--post-type",
        "-t",
        help="Тип контента для удаления (posts, pages, service, и т.д.)"
    ),
    status: str = typer.Option(
        "any",
        "--status",
        help="Статус записей для удаления (any, publish, draft, etc.)"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Удалить полностью (минуя корзину)"
    ),
    delete_media: bool = typer.Option(
        False,
        "--delete-media",
        help="Удалять связанные featured media"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Показать что будет удалено, без удаления"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
):
    """
    Массовое удаление всех записей указанного типа

    Примеры:
        # Удалить все черновики постов в корзину (используя .env)
        python -m src.cli bulk-delete --post-type posts --status draft

        # Удалить с явным указанием credentials
        python -m src.cli bulk-delete --domain https://example.com --username master --password "XimY D2BP NPJr khQj PSbn A8ax" --post-type posts --status draft

        # Удалить все записи типа service полностью с медиа
        python -m src.cli bulk-delete --post-type service --force --delete-media

        # Показать что будет удалено (dry-run)
        python -m src.cli bulk-delete --post-type posts --dry-run

        # Использовать кастомный .env файл
        python -m src.cli bulk-delete --env-file project2/.env --post-type posts
    """
    console.print("\n[bold red]Bulk Delete[/bold red]")
    console.print("=" * 60 + "\n")

    try:
        # Настройка логирования
        logs_folder = Path("logs")
        logs_folder.mkdir(parents=True, exist_ok=True)

        from .config import LoggingConfig
        logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="bulk_delete"
        )

        # Получаем credentials (приоритет: CLI параметры → .env файл)
        console.print(f"[yellow]→[/yellow] Загрузка credentials...")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )

        console.print(f"[green]✓[/green] WordPress: {domain}")
        console.print(f"[yellow]→[/yellow] Тип контента: {post_type}")
        console.print(f"[yellow]→[/yellow] Статус: {status}")
        console.print(f"[yellow]→[/yellow] Режим удаления: {'Полное' if force else 'В корзину'}")
        console.print(f"[yellow]→[/yellow] Удалять медиа: {'Да' if delete_media else 'Нет'}")
        if dry_run:
            console.print(f"[yellow]→[/yellow] DRY-RUN режим: изменения не будут применены")
        console.print()

        # Создаем WordPress клиент
        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        # Запускаем удаление
        stats = asyncio.run(run_bulk_delete(
            wp_config,
            post_type,
            status,
            force,
            delete_media,
            dry_run
        ))

        # Показываем результаты
        console.print("\n[bold green]Операция завершена![/bold green]")
        console.print("=" * 60)
        console.print(f"  Найдено записей: [cyan]{stats['found']}[/cyan]")
        console.print(f"  Удалено записей: [cyan]{stats['deleted']}[/cyan]")
        if delete_media:
            console.print(f"  Удалено медиа: [cyan]{stats['media_deleted']}[/cyan]")
        console.print(f"  Ошибок: [{'red' if stats['errors'] > 0 else 'green'}]{stats['errors']}[/{'red' if stats['errors'] > 0 else 'green'}]")
        console.print(f"\n[dim]Логи: {log_file}[/dim]")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]✗ Ошибка:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[bold red]✗ Неожиданная ошибка:[/bold red] {e}")
        logger.exception("Критическая ошибка")
        raise typer.Exit(1)


async def run_bulk_delete(
    wp_config: WordPressConfig,
    post_type: str,
    status: str,
    force: bool,
    delete_media: bool,
    dry_run: bool
) -> Dict[str, int]:
    """
    Выполнить массовое удаление записей.

    Args:
        wp_config: Конфигурация WordPress
        post_type: Тип контента
        status: Статус записей
        force: Удалить полностью
        delete_media: Удалять featured media
        dry_run: Режим симуляции

    Returns:
        Статистика: {found: N, deleted: N, media_deleted: N, errors: N}
    """
    from .wordpress.media import MediaEndpoint

    stats = {
        "found": 0,
        "deleted": 0,
        "media_deleted": 0,
        "errors": 0
    }

    async with WordPressClient(wp_config) as client:
        # Создаем endpoints
        posts_endpoint = PostsEndpoint(client, post_type)
        media_endpoint = MediaEndpoint(client) if delete_media else None

        # Получаем все записи указанного типа
        logger.info(f"Fetching all {post_type} with status={status}...")
        all_posts = await posts_endpoint.get_all(status=status)

        stats["found"] = len(all_posts)
        logger.info(f"Found {len(all_posts)} {post_type}")

        if dry_run:
            console.print(f"\n[yellow]DRY-RUN: Будет удалено {len(all_posts)} записей[/yellow]")
            for post in all_posts[:10]:  # Показываем первые 10
                title = post.get("title", {})
                if isinstance(title, dict):
                    title_text = title.get("rendered", "No title")
                else:
                    title_text = str(title)
                console.print(f"  ID={post['id']}: {title_text}")
            if len(all_posts) > 10:
                console.print(f"  ... и еще {len(all_posts) - 10} записей")
            return stats

        # Удаляем каждую запись
        for post in all_posts:
            try:
                post_id = post["id"]
                title = post.get("title", {})
                if isinstance(title, dict):
                    title_text = title.get("rendered", "No title")
                else:
                    title_text = str(title)

                # Удаляем featured media если указано
                if delete_media and media_endpoint:
                    featured_media_id = post.get("featured_media")
                    if featured_media_id and featured_media_id > 0:
                        try:
                            await media_endpoint.delete(featured_media_id, force=True)
                            logger.info(f"Deleted featured media ID={featured_media_id} for post ID={post_id}")
                            stats["media_deleted"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete featured media ID={featured_media_id}: {e}")

                # Удаляем запись
                await posts_endpoint.delete(post_id, force=force)
                logger.info(f"Deleted {post_type} ID={post_id}: {title_text}")
                stats["deleted"] += 1

            except Exception as e:
                logger.error(f"Failed to delete post ID={post.get('id')}: {e}")
                stats["errors"] += 1

        logger.info(f"Bulk delete completed: deleted={stats['deleted']}, errors={stats['errors']}")
        return stats


async def run_sync_async(
    config: AppConfig,
    sheets_filter: Optional[str] = None,
    test_mode: bool = False,
    retry_config=None,
    circuit_breaker=None
):
    """
    Async версия run_sync с параллельной обработкой строк.

    Отличия от run_sync:
    - WordPressClient создается с retry_config и circuit_breaker
    - Используются ContentSyncAsync, TaxonomySyncAsync
    - ExcelWriter использует update_*_async()
    """
    started_at = datetime.now()

    # Статистика
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
        # 1. Открываем Excel файл
        with ExcelReader(config.paths.excel_file) as reader:
            sheets_by_type = reader.get_sheets_by_type()

            logger.info(f"Найдено листов:")
            logger.info(f"  Content: {len(sheets_by_type['content'])}")
            logger.info(f"  Taxonomy: {len(sheets_by_type['taxonomy'])}")

            # Фильтруем листы если указано
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

            # 2. Определяем timeout
            timeout = config.sync.timeout if hasattr(config.sync, 'timeout') else 30

            # 3. Подключаемся к WordPress API с retry и circuit breaker
            async with WordPressClient(
                config.wordpress,
                retry_config=retry_config,
                circuit_breaker=circuit_breaker,
                timeout=timeout
            ) as wp_client:
                # Проверка подключения
                await wp_client.test_connection()

                # 4. Загружаем кэш таксономий
                logger.info("Загрузка кэша таксономий...")
                taxonomy_cache = TaxonomyCache()

                # Определяем какие таксономии используются
                taxonomies_to_cache = set()
                for sheet_name in sheets_to_process:
                    sheet_type = reader.classify_sheet(sheet_name)
                    if sheet_type == "content":
                        df = reader.read_sheet(sheet_name)
                        # Ищем колонки таксономий
                        for col in df.columns:
                            if col in ["categories", "tags"] or col.endswith("-cat"):
                                taxonomies_to_cache.add(col)
                    elif sheet_type == "taxonomy":
                        taxonomies_to_cache.add(sheet_name)

                if taxonomies_to_cache:
                    await taxonomy_cache.load(wp_client, list(taxonomies_to_cache))

                # 5. Создаем ASYNC sync классы
                content_sync = ContentSyncAsync(wp_client, config, taxonomy_cache)
                taxonomy_sync = TaxonomySyncAsync(wp_client, config)
                media_sync = MediaSync(wp_client, config)  # MediaSync уже async

                # 6. Открываем Excel writer (если нужно)
                excel_writer = None
                if config.sync.update_excel:
                    excel_writer = ExcelWriter(
                        config.paths.excel_file,
                        taxonomy_cache=taxonomy_cache
                    )
                    excel_writer.load()

                # 7. Обработка каждого листа (ПОСЛЕДОВАТЕЛЬНО!)
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

                    # Обновляем общую статистику
                    stats["overall"]["total"] += sheet_stats.get("total", 0)
                    stats["overall"]["successful"] += (
                        sheet_stats.get("added", 0) +
                        sheet_stats.get("updated", 0) +
                        sheet_stats.get("deleted", 0)
                    )
                    stats["overall"]["errors"] += sheet_stats.get("errors", 0)

                # 8. Сохраняем Excel
                if excel_writer:
                    excel_writer.save()

        # Вычисляем success rate (от обработанных, без skipped)
        # Подсчитываем skipped из всех листов
        total_skipped = sum(
            sheet.get("skipped", 0) for sheet in stats["sheets"].values()
        )
        processed = stats["overall"]["total"] - total_skipped
        if processed > 0:
            stats["overall"]["success_rate"] = (
                stats["overall"]["successful"] / processed
            ) * 100
        elif stats["overall"]["total"] > 0:
            # Все строки skipped — 100% success rate
            stats["overall"]["success_rate"] = 100.0

        # Время завершения
        finished_at = datetime.now()
        stats["finished_at"] = finished_at.isoformat()
        stats["duration_seconds"] = (finished_at - started_at).total_seconds()

        # Выводим summary
        log_summary(stats, config.paths.logs_folder / "async_sync.log")

    except Exception as e:
        logger.exception("Ошибка async синхронизации")
        raise


async def process_sheet_async(
    reader: ExcelReader,
    content_sync: ContentSyncAsync,
    media_sync,  # MediaSync
    taxonomy_sync: TaxonomySyncAsync,
    excel_writer: Optional[ExcelWriter],
    sheet_name: str,
    config: AppConfig,
    test_mode: bool = False
) -> dict:
    """
    Async версия process_sheet.

    Args:
        test_mode: Тестовый режим (обрабатывает только первые 3 строки)

    Returns:
        Dict со статистикой
    """
    try:
        # Читаем лист
        df = reader.read_sheet(sheet_name)

        logger.info(f"Всего строк: {len(df)}")

        # В тестовом режиме берем только первые 3 строки
        if test_mode:
            original_len = len(df)
            df = df.head(3)
            logger.info(f"ТЕСТОВЫЙ РЕЖИМ: Обрабатываем только первые {len(df)} строк из {original_len}")

        # Проверяем есть ли колонка action
        if "action" not in df.columns:
            logger.warning(f"Лист {sheet_name} не содержит колонку 'action', пропускаем")
            return {
                "total": len(df),
                "added": 0,
                "updated": 0,
                "deleted": 0,
                "skipped": len(df),
                "errors": 0
            }

        # Синхронизируем через соответствующий async sync
        sheet_type = reader.classify_sheet(sheet_name)

        if sheet_type == "content":
            # Асинхронная синхронизация контента
            stats = await content_sync.sync_sheet(
                sheet_name,
                df,
                excel_writer
            )
            stats["total"] = len(df)
            return stats

        elif sheet_type == "media":
            # Синхронизация медиафайлов (уже async)
            stats = await media_sync.sync_media(
                df,
                excel_writer
            )
            stats["total"] = len(df)
            return stats

        elif sheet_type == "taxonomy":
            # Асинхронная синхронизация таксономий
            stats = await taxonomy_sync.sync_sheet(
                sheet_name,
                df,
                excel_writer
            )
            stats["total"] = len(df)
            return stats

        else:
            logger.warning(f"Неизвестный тип листа: {sheet_name}")
            return {
                "total": len(df),
                "added": 0,
                "updated": 0,
                "deleted": 0,
                "skipped": len(df),
                "errors": 0
            }

    except Exception as e:
        logger.error(f"Ошибка обработки листа {sheet_name}: {e}")
        logger.exception(e)
        return {
            "total": 0,
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "skipped": 0,
            "errors": 1
        }


@app.command()
def counter(
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
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    post_type: str = typer.Option(
        "posts",
        "--type",
        "-t",
        help="Тип контента (posts, pages, или custom post type)"
    ),
    status: str = typer.Option(
        "publish",
        "--status",
        "-s",
        help="Статус записей (publish, draft, any)"
    ),
    output: Path = typer.Option(
        Path("counter_output.csv"),
        "--output",
        "-o",
        help="Путь к выходному CSV файлу"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Максимум записей для обработки"
    ),
    concurrency: int = typer.Option(
        5,
        "--concurrency",
        "-c",
        help="Количество параллельных запросов"
    )
):
    """
    Подсчет количества слов и символов в контенте записей WordPress.

    Выводит результаты в CSV файл с полями:
    id, slug, link, title, words, chars

    Пример:
        wp-bulk counter --domain https://example.com --type posts --output stats.csv
    """
    console.print(f"\n[bold blue]WordPress Bulk Editor - Counter[/bold blue]\n")

    try:
        # 1. Загружаем credentials
        console.print("[yellow]Загрузка credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

        # 2. Запускаем асинхронный подсчет
        asyncio.run(count_content_stats(
            domain=domain,
            username=username,
            password=password,
            post_type=post_type,
            status=status,
            output_file=output,
            limit=limit,
            concurrency=concurrency
        ))

    except ValueError as e:
        console.print(f"\n[bold red]VALIDATION ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        logger.exception("Ошибка при подсчете статистики")
        raise typer.Exit(code=1)


async def count_content_stats(
    domain: str,
    username: str,
    password: str,
    post_type: str,
    status: str,
    output_file: Path,
    limit: Optional[int],
    concurrency: int
):
    """
    Асинхронный подсчет статистики контента.
    """
    import csv
    import html
    from bs4 import BeautifulSoup

    wp_config = WordPressConfig(
        url=domain,
        username=username,
        password=password
    )

    results = []

    async with WordPressClient(wp_config) as client:
        # Проверка подключения
        await client.test_connection()
        console.print("[green]OK[/green] Подключение к WordPress установлено\n")

        # Создаем endpoint
        posts_endpoint = PostsEndpoint(client, post_type)

        # Получаем все записи
        console.print(f"[yellow]Получение записей типа '{post_type}'...[/yellow]")

        # Формируем параметры
        params = {}
        if status != "any":
            params["status"] = status

        # Получаем все записи постранично
        all_posts = []
        page = 1
        per_page = 100

        while True:
            posts = await posts_endpoint.list(per_page=per_page, page=page, **params)

            if not posts:
                break

            all_posts.extend(posts)
            console.print(f"  Загружено: {len(all_posts)} записей...")

            if len(posts) < per_page:
                break

            if limit and len(all_posts) >= limit:
                all_posts = all_posts[:limit]
                break

            page += 1

        console.print(f"[green]OK[/green] Всего записей: {len(all_posts)}\n")

        if not all_posts:
            console.print("[yellow]Нет записей для обработки[/yellow]")
            return

        # Обрабатываем каждую запись
        console.print("[yellow]Подсчет статистики...[/yellow]")

        def count_words_and_chars(html_content: str) -> tuple[int, int]:
            """Подсчет слов и символов в HTML контенте."""
            if not html_content:
                return 0, 0

            # Убираем HTML теги
            soup = BeautifulSoup(html_content, "html.parser")
            text = soup.get_text(separator=" ")

            # Декодируем HTML entities
            text = html.unescape(text)

            # Убираем лишние пробелы
            text = " ".join(text.split())

            # Подсчет
            chars = len(text)
            words = len(text.split()) if text else 0

            return words, chars

        for post in all_posts:
            # Извлекаем контент
            content = post.get("content", {})
            if isinstance(content, dict):
                content_html = content.get("rendered", "")
            else:
                content_html = str(content) if content else ""

            # Извлекаем title
            title = post.get("title", {})
            if isinstance(title, dict):
                title_text = title.get("rendered", "")
            else:
                title_text = str(title) if title else ""

            # Подсчитываем
            words, chars = count_words_and_chars(content_html)

            results.append({
                "id": post.get("id", ""),
                "slug": post.get("slug", ""),
                "link": post.get("link", ""),
                "title": html.unescape(BeautifulSoup(title_text, "html.parser").get_text()),
                "words": words,
                "chars": chars
            })

        console.print(f"[green]OK[/green] Обработано: {len(results)} записей\n")

    # Записываем в CSV
    console.print(f"[yellow]Запись в CSV: {output_file}[/yellow]")

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "slug", "link", "title", "words", "chars"],
            delimiter=";"
        )
        writer.writeheader()
        writer.writerows(results)

    console.print(f"[green]OK[/green] Файл сохранен: {output_file}\n")

    # Выводим статистику
    total_words = sum(r["words"] for r in results)
    total_chars = sum(r["chars"] for r in results)
    avg_words = total_words / len(results) if results else 0
    avg_chars = total_chars / len(results) if results else 0

    console.print("[bold]Статистика:[/bold]")
    console.print(f"  Всего записей: {len(results)}")
    console.print(f"  Всего слов: {total_words:,}")
    console.print(f"  Всего символов: {total_chars:,}")
    console.print(f"  Среднее слов на запись: {avg_words:.1f}")
    console.print(f"  Среднее символов на запись: {avg_chars:.1f}")
    console.print()


@app.command(name="async")
def async_sync(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
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
    🚀 Асинхронная синхронизация Excel → WordPress

    Отличия от sync:
    - Параллельная обработка строк (Semaphore)
    - Retry с exponential backoff
    - Circuit breaker для устойчивости
    - Настраиваемые таймауты
    """
    console.print(f"\n[bold cyan]🚀 WordPress Bulk Editor - Async Sync[/bold cyan]\n")
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

    try:
        # 1. Загружаем credentials (приоритет: CLI параметры → .env файл)
        console.print("[yellow]Загрузка credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )
        console.print(f"[green]OK[/green] WordPress: {domain}")
        console.print(f"[green]OK[/green] User: {username}\n")

        # 2. Загружаем async конфигурацию (приоритет CLI > .env > default)
        concurrency_val, retry_cfg, circuit_breaker_obj = load_async_config_from_env(
            env_file=env_file,
            cli_concurrency=concurrency,
            cli_max_retries=max_retries
        )

        if not enable_circuit_breaker:
            circuit_breaker_obj = None

        # 3. Создаем конфигурацию
        config = AppConfig.from_excel(
            excel_file=file,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
            sync=SyncConfig(
                dry_run=dry_run,
                concurrency=concurrency_val,
                skip_media=skip_media,
                update_excel=not no_update_excel,
                delete_featured_media=delete_media,
                skip_existing_media=skip_existing_media,
                timeout=api_timeout or 30
            ),
            logging=LoggingConfig(
                level="DEBUG" if verbose else "INFO"
            )
        )

        # 4. Настройка логирования
        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="async_sync"
        )

        # 5. Запускаем async синхронизацию
        asyncio.run(run_sync_async(
            config=config,
            sheets_filter=sheets,
            test_mode=test_mode,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj
        ))

        console.print(f"\n[bold green]SUCCESS! Асинхронная синхронизация завершена![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


@app.command(name="async-from-google")
def async_sync_from_google(
    sheet_id: Optional[str] = typer.Option(
        None,
        "--sheet-id",
        help="ID Google Sheets документа"
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Путь к JSON файлу с credentials"
    ),
    output: Path = typer.Option(
        Path("export.xlsx"),
        "--output",
        "-o",
        help="Путь для сохранения .xlsx файла"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов через запятую (posts,pages)"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Режим dry-run"
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
        help="Параллельные запросы (default: 5)"
    ),
    max_retries: Optional[int] = typer.Option(
        None,
        "--max-retries",
        help="Максимум retry (default: 3)"
    ),
    enable_circuit_breaker: bool = typer.Option(
        True,
        "--circuit-breaker/--no-circuit-breaker",
        help="Circuit breaker"
    ),
    skip_media: bool = typer.Option(
        False,
        "--skip-media",
        help="Пропустить медиа"
    ),
    no_update_excel: bool = typer.Option(
        False,
        "--no-update-excel",
        help="Не писать ID в Excel"
    ),
    delete_media: bool = typer.Option(
        False,
        "--delete-media",
        help="Удалять media при delete"
    ),
    skip_existing_media: bool = typer.Option(
        False,
        "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update"
    ),
):
    """
    🚀 Async Google Sheets → WordPress

    1. Скачать Google Sheets
    2. Async синхронизация с WordPress
    """
    console.print(f"\n[bold cyan]🚀 Google Sheets → WordPress (Async)[/bold cyan]\n")

    try:
        import os
        from dotenv import load_dotenv
        from .utils.google_sheets import GoogleSheetsClient

        # ========== ШАГ 1: СКАЧИВАНИЕ GOOGLE SHEETS ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        # Загружаем .env
        load_dotenv()

        # Получаем параметры (приоритет: CLI > .env)
        final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

        # Валидация
        if not final_sheet_id:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_ID")
            raise typer.Exit(1)

        if not final_credentials:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_CREDENTIALS_PATH")
            raise typer.Exit(1)

        credentials_path = Path(final_credentials)
        if not credentials_path.exists():
            console.print(f"[red]✗[/red] Файл credentials не найден: {credentials_path}")
            raise typer.Exit(1)

        # Скачиваем
        gs_client = GoogleSheetsClient(credentials_path)
        info = gs_client.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]✓[/green] Документ: {info.get('name', 'Unknown')}")

        gs_client.download_as_excel(final_sheet_id, output)
        console.print(f"[bold green]✓ Файл скачан![/bold green]\n")

        # ========== ШАГ 2: ASYNC СИНХРОНИЗАЦИЯ ==========
        console.print("[bold yellow]ШАГ 2: Async синхронизация с WordPress[/bold yellow]\n")

        # Загружаем credentials
        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

        # Загружаем async конфигурацию
        concurrency_val, retry_cfg, circuit_breaker_obj = load_async_config_from_env(
            env_file=env_file,
            cli_concurrency=concurrency,
            cli_max_retries=max_retries
        )

        if not enable_circuit_breaker:
            circuit_breaker_obj = None

        # Создаем конфигурацию
        config = AppConfig.from_excel(
            excel_file=output,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
            sync=SyncConfig(
                dry_run=dry_run,
                concurrency=concurrency_val,
                skip_media=skip_media,
                update_excel=not no_update_excel,
                delete_featured_media=delete_media,
                skip_existing_media=skip_existing_media
            ),
            logging=LoggingConfig(level="DEBUG" if verbose else "INFO")
        )

        # Настройка логирования
        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="async_from_google"
        )

        # Запускаем async синхронизацию
        asyncio.run(run_sync_async(
            config=config,
            sheets_filter=sheets,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj
        ))

        console.print(f"\n[bold green]SUCCESS! Пайплайн завершен![/bold green]")
        console.print(f"Файл: [cyan]{output}[/cyan]")
        console.print(f"Лог: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Ошибка")
        raise typer.Exit(code=1)


@app.command(name="async-google-bidirectional")
def async_sync_google_bidirectional(
    sheet_id: Optional[str] = typer.Option(
        None,
        "--sheet-id",
        help="ID Google Sheets документа"
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Путь к JSON файлу с credentials"
    ),
    output: Path = typer.Option(
        Path("export.xlsx"),
        "--output",
        "-o",
        help="Путь для сохранения .xlsx файла"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов через запятую"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Режим dry-run"
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
        help="Параллельные запросы (default: 5)"
    ),
    max_retries: Optional[int] = typer.Option(
        None,
        "--max-retries",
        help="Максимум retry (default: 3)"
    ),
    enable_circuit_breaker: bool = typer.Option(
        True,
        "--circuit-breaker/--no-circuit-breaker",
        help="Circuit breaker"
    ),
    skip_media: bool = typer.Option(
        False,
        "--skip-media",
        help="Пропустить медиа"
    ),
    no_update_excel: bool = typer.Option(
        False,
        "--no-update-excel",
        help="Не писать ID в Excel"
    ),
    no_upload_back: bool = typer.Option(
        False,
        "--no-upload-back",
        help="Не загружать обратно в Google Sheets"
    ),
    delete_media: bool = typer.Option(
        False,
        "--delete-media",
        help="Удалять media при delete"
    ),
    skip_existing_media: bool = typer.Option(
        False,
        "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update"
    ),
):
    """
    🚀 Async Google Sheets ↔ WordPress ↔ Google Sheets

    1. Скачать Google Sheets
    2. Async синхронизация с WordPress
    3. Загрузить обратно в Google Sheets
    """
    console.print(f"\n[bold cyan]🚀 Google Sheets ↔ WordPress ↔ Google Sheets (Async)[/bold cyan]\n")

    try:
        import os
        from dotenv import load_dotenv
        from .utils.google_sheets import GoogleSheetsClient

        # ========== ШАГ 1: СКАЧИВАНИЕ ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        load_dotenv()

        final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

        if not final_sheet_id or not final_credentials:
            console.print("[red]✗[/red] Необходимо указать GOOGLE_SHEET_ID и GOOGLE_SHEET_CREDENTIALS_PATH")
            raise typer.Exit(1)

        credentials_path = Path(final_credentials)
        if not credentials_path.exists():
            console.print(f"[red]✗[/red] Credentials не найден: {credentials_path}")
            raise typer.Exit(1)

        gs_client = GoogleSheetsClient(credentials_path)
        info = gs_client.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]✓[/green] Документ: {info.get('name', 'Unknown')}")

        gs_client.download_as_excel(final_sheet_id, output)
        console.print(f"[bold green]✓ Файл скачан![/bold green]\n")

        # ========== ШАГ 2: ASYNC СИНХРОНИЗАЦИЯ ==========
        console.print("[bold yellow]ШАГ 2: Async синхронизация с WordPress[/bold yellow]\n")

        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

        concurrency_val, retry_cfg, circuit_breaker_obj = load_async_config_from_env(
            env_file=env_file,
            cli_concurrency=concurrency,
            cli_max_retries=max_retries
        )

        if not enable_circuit_breaker:
            circuit_breaker_obj = None

        config = AppConfig.from_excel(
            excel_file=output,
            wp_url=domain,
            wp_username=username,
            wp_password=password,
            sync=SyncConfig(
                dry_run=dry_run,
                concurrency=concurrency_val,
                skip_media=skip_media,
                update_excel=not no_update_excel,
                delete_featured_media=delete_media,
                skip_existing_media=skip_existing_media
            ),
            logging=LoggingConfig(level="DEBUG" if verbose else "INFO")
        )

        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="async_google_bidirectional"
        )

        asyncio.run(run_sync_async(
            config=config,
            sheets_filter=sheets,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj
        ))

        console.print(f"\n[bold green]✓ Синхронизация завершена![/bold green]\n")

        # ========== ШАГ 3: ЗАГРУЗКА ОБРАТНО ==========
        if not no_upload_back and not dry_run:
            console.print("[bold yellow]ШАГ 3: Загрузка обратно в Google Sheets[/bold yellow]\n")

            sheets_to_upload = [s.strip() for s in sheets.split(",")] if sheets else None

            gs_client.upload_excel_to_sheets(
                excel_path=output,
                spreadsheet_id=final_sheet_id,
                sheets_to_update=sheets_to_upload
            )

            console.print(f"[bold green]✓ Загружено в Google Sheets![/bold green]\n")
        elif dry_run:
            console.print("[yellow]ШАГ 3 пропущен: DRY-RUN режим[/yellow]\n")
        else:
            console.print("[yellow]ШАГ 3 пропущен: --no-upload-back[/yellow]\n")

        console.print(f"[bold green]SUCCESS! Полный пайплайн завершен![/bold green]")
        console.print(f"Файл: [cyan]{output}[/cyan]")
        console.print(f"Лог: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Ошибка")
        raise typer.Exit(code=1)


def main():
    """Точка входа"""
    app()


if __name__ == "__main__":
    main()
