"""
Команды работы с Google Sheets:
download-google-sheet, upload-to-google-sheets,
sync-from-google, sync-google-bidirectional,
async-from-google, async-google-bidirectional
"""

from pathlib import Path
from typing import Optional
import asyncio

from loguru import logger

from . import app, console
from .common import handle_command_errors, load_and_print_credentials
from .helpers import get_project, resolve_env_file, resolve_logs_folder, resolve_output
from ..config import AppConfig, SyncConfig, LoggingConfig
from ..utils.logger import setup_logger
from ..utils.auth import load_async_config_from_env

import typer


# ============================================================
# Helpers для загрузки Google Sheets параметров
# ============================================================

def _load_google_params(sheet_id: Optional[str], credentials: Optional[str], env_file: Optional[Path] = None):
    """
    Загрузить параметры Google Sheets из CLI или .env.

    Returns:
        (sheet_id, credentials_path)
    """
    import os
    from dotenv import load_dotenv

    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    final_sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
    final_credentials = credentials or os.getenv("GOOGLE_SHEET_CREDENTIALS_PATH")

    if not final_sheet_id:
        console.print("[red]ERROR: Необходимо указать GOOGLE_SHEET_ID (через --sheet-id или .env)[/red]")
        raise typer.Exit(1)

    if not final_credentials:
        console.print("[red]ERROR: Необходимо указать GOOGLE_SHEET_CREDENTIALS_PATH (через --credentials или .env)[/red]")
        raise typer.Exit(1)

    credentials_path = Path(final_credentials)
    if not credentials_path.exists():
        console.print(f"[red]ERROR: Файл credentials не найден: {credentials_path}[/red]")
        raise typer.Exit(1)

    return final_sheet_id, credentials_path


# ============================================================
# download-google-sheet
# ============================================================

@app.command()
def download_google_sheet(
    ctx: typer.Context,
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
    env_file = resolve_env_file(ctx, None)
    output = resolve_output(ctx, output, "export.xlsx")

    console.print("\n[bold cyan]Google Sheets Downloader[/bold cyan]")
    console.print("=" * 60 + "\n")

    # Настройка логирования
    logs_folder = resolve_logs_folder(ctx)
    logs_folder.mkdir(parents=True, exist_ok=True)

    logging_config = LoggingConfig()

    log_file = setup_logger(
        logs_folder=logs_folder,
        config=logging_config,
        command_name="download_google_sheet"
    )
    logger.info(f"Лог-файл создан: {log_file}")

    with handle_command_errors():
        from ..utils.google_sheets import GoogleSheetsClient

        final_sheet_id, credentials_path = _load_google_params(
            sheet_id, str(credentials) if credentials else None, env_file=env_file
        )

        console.print(f"[yellow]->[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]->[/yellow] Credentials: {credentials_path}")
        console.print(f"[yellow]->[/yellow] Файл назначения: {output}")
        console.print()

        # Скачиваем
        console.print("[yellow]Скачивание Google Sheets...[/yellow]")

        downloader = GoogleSheetsClient(credentials_path)

        info = downloader.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]OK[/green] Документ: {info.get('name', 'Unknown')}")
        console.print(f"[green]OK[/green] Последнее изменение: {info.get('modified_time', 'Unknown')}")
        console.print()

        downloader.download_as_excel(final_sheet_id, output)

        console.print(f"\n[bold green]OK Файл успешно скачан![/bold green]")
        console.print(f"[bold]Сохранено:[/bold] {output}")


# ============================================================
# upload-to-google-sheets
# ============================================================

@app.command()
def upload_to_google_sheets(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу для загрузки (авто-определяется из --project)",
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

    Примеры:
        # Загрузить весь файл (используя .env)
        python -m src.cli upload-to-google-sheets --file export.xlsx

        # Загрузить только определенные листы
        python -m src.cli upload-to-google-sheets --file export.xlsx --sheets posts,pages
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)

    if file is None:
        file = proj.find_excel() if proj.active else None
        if file is None:
            console.print("[red]ERROR: Укажите --file или используйте --project с xlsx-файлом в папке проекта[/red]")
            raise typer.Exit(code=1)
        console.print(f"[dim]Excel файл (авто): {file.name}[/dim]")

    console.print("\n[bold cyan]Upload Excel -> Google Sheets[/bold cyan]")
    console.print("=" * 60 + "\n")

    # Настройка логирования
    logs_folder = resolve_logs_folder(ctx)
    logs_folder.mkdir(parents=True, exist_ok=True)
    logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")

    log_file = setup_logger(
        logs_folder=logs_folder,
        config=logging_config,
        command_name="upload_to_google_sheets"
    )
    logger.info(f"Лог-файл создан: {log_file}")

    with handle_command_errors():
        from ..utils.google_sheets import GoogleSheetsClient

        final_sheet_id, credentials_path = _load_google_params(
            sheet_id, str(credentials) if credentials else None, env_file=env_file
        )

        console.print(f"[yellow]->[/yellow] Excel файл: {file}")
        console.print(f"[yellow]->[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]->[/yellow] Credentials: {credentials_path}")

        sheets_to_upload = None
        if sheets:
            sheets_to_upload = [s.strip() for s in sheets.split(",")]
            console.print(f"[yellow]->[/yellow] Листы для обновления: {', '.join(sheets_to_upload)}")
        else:
            console.print(f"[yellow]->[/yellow] Будут обновлены все листы из Excel файла")

        console.print()

        console.print("[yellow]Загрузка в Google Sheets...[/yellow]")
        gs_client = GoogleSheetsClient(credentials_path)

        gs_client.upload_excel_to_sheets(
            excel_path=file,
            spreadsheet_id=final_sheet_id,
            sheets_to_update=sheets_to_upload
        )

        console.print(f"\n[bold green]OK Данные успешно загружены в Google Sheets![/bold green]")
        console.print(f"[bold]Локальный файл:[/bold] {file}\n")


# ============================================================
# sync-from-google
# ============================================================

@app.command()
def sync_from_google(
    ctx: typer.Context,
    sheet_id: Optional[str] = typer.Option(None, "--sheet-id", help="ID Google Sheets документа"),
    credentials: Optional[Path] = typer.Option(None, "--credentials", help="Путь к JSON файлу с credentials"),
    output: Path = typer.Option(Path("export.xlsx"), "--output", "-o", help="Путь для сохранения .xlsx файла"),
    sheets: Optional[str] = typer.Option(None, "--sheets", help="Список листов через запятую (posts,pages)"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run (без изменений)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: int = typer.Option(5, "--concurrency", help="Количество параллельных запросов"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить загрузку медиа"),
    skip_tax: bool = typer.Option(False, "--skip-tax", help="Пропустить обновление таксономий"),
    no_update_excel: bool = typer.Option(False, "--no-update-excel", help="Не записывать ID обратно в Excel"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять featured media при удалении записей"),
    skip_existing_media: bool = typer.Option(False, "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update"),
):
    """
    Скачать Google Sheets и синхронизировать с WordPress (пайплайн)

    1. Скачивает Google Sheets документ в формате Excel
    2. Выполняет синхронизацию с WordPress

    Примеры:
        python -m src.cli sync-from-google
        python -m src.cli sync-from-google --sheets posts,pages
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "export.xlsx")

    console.print("\n[bold cyan]Google Sheets -> WordPress Pipeline[/bold cyan]")
    console.print("=" * 60 + "\n")

    with handle_command_errors():
        from ..utils.google_sheets import GoogleSheetsClient
        from .sync_commands import run_sync

        # ========== ШАГ 1: СКАЧИВАНИЕ GOOGLE SHEETS ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        final_sheet_id, credentials_path = _load_google_params(
            sheet_id, str(credentials) if credentials else None, env_file=env_file
        )

        console.print(f"[yellow]->[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]->[/yellow] Credentials: {credentials_path}")
        console.print(f"[yellow]->[/yellow] Файл назначения: {output}")
        console.print()

        console.print("[yellow]Скачивание Google Sheets...[/yellow]")
        downloader = GoogleSheetsClient(credentials_path)

        info = downloader.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]OK[/green] Документ: {info.get('name', 'Unknown')}")
        console.print(f"[green]OK[/green] Последнее изменение: {info.get('modified_time', 'Unknown')}")
        console.print()

        downloader.download_as_excel(final_sheet_id, output)

        console.print(f"[bold green]OK Файл успешно скачан![/bold green]")
        console.print(f"[bold]Сохранено:[/bold] {output}\n")

        # ========== ШАГ 2: СИНХРОНИЗАЦИЯ С WORDPRESS ==========
        console.print("[bold yellow]ШАГ 2: Синхронизация с WordPress[/bold yellow]\n")

        domain, username, password = load_and_print_credentials(env_file=env_file)

        config = AppConfig.from_excel(
            excel_file=output,
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

        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="sync_from_google"
        )

        asyncio.run(run_sync(config, sheets))

        console.print(f"\n[bold green]SUCCESS! Пайплайн завершен![/bold green]")
        console.print(f"Скачанный файл: [cyan]{output}[/cyan]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")


# ============================================================
# sync-google-bidirectional
# ============================================================

@app.command()
def sync_google_bidirectional(
    ctx: typer.Context,
    sheet_id: Optional[str] = typer.Option(None, "--sheet-id", help="ID Google Sheets документа"),
    credentials: Optional[Path] = typer.Option(None, "--credentials", help="Путь к JSON файлу с credentials"),
    output: Path = typer.Option(Path("export.xlsx"), "--output", "-o", help="Путь для сохранения .xlsx файла"),
    sheets: Optional[str] = typer.Option(None, "--sheets",
        help="Список листов для обновления через запятую (posts,pages)"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run (без изменений)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: int = typer.Option(5, "--concurrency", help="Количество параллельных запросов"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить загрузку медиа"),
    skip_tax: bool = typer.Option(False, "--skip-tax", help="Пропустить обновление таксономий"),
    no_update_excel: bool = typer.Option(False, "--no-update-excel", help="Не записывать ID обратно в Excel"),
    no_upload_back: bool = typer.Option(False, "--no-upload-back",
        help="Не загружать обратно в Google Sheets после синхронизации"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять featured media при удалении записей"),
    skip_existing_media: bool = typer.Option(False, "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update"),
):
    """
    Двусторонняя синхронизация: Google Sheets <-> WordPress <-> Google Sheets

    1. Скачивает Google Sheets документ в формате Excel
    2. Выполняет синхронизацию с WordPress
    3. Загружает обновленный файл обратно в Google Sheets

    Примеры:
        python -m src.cli sync-google-bidirectional
        python -m src.cli sync-google-bidirectional --sheets posts,pages
        python -m src.cli sync-google-bidirectional --no-upload-back
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "export.xlsx")

    console.print("\n[bold cyan]Google Sheets <-> WordPress <-> Google Sheets Pipeline[/bold cyan]")
    console.print("=" * 60 + "\n")

    with handle_command_errors():
        from ..utils.google_sheets import GoogleSheetsClient
        from .sync_commands import run_sync

        # ========== ШАГ 1: СКАЧИВАНИЕ GOOGLE SHEETS ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        final_sheet_id, credentials_path = _load_google_params(
            sheet_id, str(credentials) if credentials else None, env_file=env_file
        )

        console.print(f"[yellow]->[/yellow] Google Sheets ID: {final_sheet_id}")
        console.print(f"[yellow]->[/yellow] Credentials: {credentials_path}")
        console.print(f"[yellow]->[/yellow] Файл назначения: {output}")
        console.print()

        console.print("[yellow]Скачивание Google Sheets...[/yellow]")
        gs_client = GoogleSheetsClient(credentials_path)

        info = gs_client.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]OK[/green] Документ: {info.get('name', 'Unknown')}")
        console.print(f"[green]OK[/green] Последнее изменение: {info.get('modified_time', 'Unknown')}")
        console.print()

        gs_client.download_as_excel(final_sheet_id, output)

        console.print(f"[bold green]OK Файл успешно скачан![/bold green]")
        console.print(f"[bold]Сохранено:[/bold] {output}\n")

        # ========== ШАГ 2: СИНХРОНИЗАЦИЯ С WORDPRESS ==========
        console.print("[bold yellow]ШАГ 2: Синхронизация с WordPress[/bold yellow]\n")

        domain, username, password = load_and_print_credentials(env_file=env_file)

        config = AppConfig.from_excel(
            excel_file=output,
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

        config.paths.ensure_dirs()
        log_file = setup_logger(
            logs_folder=config.paths.logs_folder,
            config=config.logging,
            command_name="sync_google_bidirectional"
        )

        asyncio.run(run_sync(config, sheets))

        console.print(f"\n[bold green]OK Синхронизация с WordPress завершена![/bold green]\n")

        # ========== ШАГ 3: ЗАГРУЗКА ОБРАТНО В GOOGLE SHEETS ==========
        if not no_upload_back and not dry_run:
            console.print("[bold yellow]ШАГ 3: Загрузка обратно в Google Sheets[/bold yellow]\n")

            sheets_to_upload = None
            if sheets:
                sheets_to_upload = [s.strip() for s in sheets.split(",")]
                console.print(f"[yellow]->[/yellow] Листы для обновления: {', '.join(sheets_to_upload)}")
            else:
                console.print(f"[yellow]->[/yellow] Будут обновлены все листы из Excel файла")

            console.print()
            console.print("[yellow]Загрузка в Google Sheets...[/yellow]")

            gs_client.upload_excel_to_sheets(
                excel_path=output,
                spreadsheet_id=final_sheet_id,
                sheets_to_update=sheets_to_upload
            )

            console.print(f"[bold green]OK Данные успешно загружены в Google Sheets![/bold green]\n")
        elif dry_run:
            console.print("[yellow]ШАГ 3 пропущен: DRY-RUN режим[/yellow]\n")
        else:
            console.print("[yellow]ШАГ 3 пропущен: --no-upload-back флаг[/yellow]\n")

        # ========== ИТОГИ ==========
        console.print(f"[bold green]SUCCESS! Полный пайплайн завершен![/bold green]")
        console.print(f"Локальный файл: [cyan]{output}[/cyan]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")


# ============================================================
# async-from-google
# ============================================================

@app.command(name="async-from-google")
def async_sync_from_google(
    ctx: typer.Context,
    sheet_id: Optional[str] = typer.Option(None, "--sheet-id", help="ID Google Sheets документа"),
    credentials: Optional[Path] = typer.Option(None, "--credentials", help="Путь к JSON файлу с credentials"),
    output: Path = typer.Option(Path("export.xlsx"), "--output", "-o", help="Путь для сохранения .xlsx файла"),
    sheets: Optional[str] = typer.Option(None, "--sheets", help="Список листов через запятую (posts,pages)"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="Параллельные запросы (default: 5)"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Максимум retry (default: 3)"),
    enable_circuit_breaker: bool = typer.Option(True, "--circuit-breaker/--no-circuit-breaker",
        help="Circuit breaker"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить медиа"),
    no_update_excel: bool = typer.Option(False, "--no-update-excel", help="Не писать ID в Excel"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять media при delete"),
    skip_existing_media: bool = typer.Option(False, "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update"),
):
    """
    Async Google Sheets -> WordPress

    1. Скачать Google Sheets
    2. Async синхронизация с WordPress
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "export.xlsx")

    console.print(f"\n[bold cyan]Google Sheets -> WordPress (Async)[/bold cyan]\n")

    with handle_command_errors():
        from ..utils.google_sheets import GoogleSheetsClient
        from .sync_commands import run_sync_async

        # ========== ШАГ 1: СКАЧИВАНИЕ GOOGLE SHEETS ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        final_sheet_id, credentials_path = _load_google_params(
            sheet_id, str(credentials) if credentials else None, env_file=env_file
        )

        gs_client = GoogleSheetsClient(credentials_path)
        info = gs_client.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]OK[/green] Документ: {info.get('name', 'Unknown')}")

        gs_client.download_as_excel(final_sheet_id, output)
        console.print(f"[bold green]OK Файл скачан![/bold green]\n")

        # ========== ШАГ 2: ASYNC СИНХРОНИЗАЦИЯ ==========
        console.print("[bold yellow]ШАГ 2: Async синхронизация с WordPress[/bold yellow]\n")

        domain, username, password = load_and_print_credentials(env_file=env_file)

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
            project_root=proj.root,
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
            command_name="async_from_google"
        )

        asyncio.run(run_sync_async(
            config=config,
            sheets_filter=sheets,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj
        ))

        console.print(f"\n[bold green]SUCCESS! Пайплайн завершен![/bold green]")
        console.print(f"Файл: [cyan]{output}[/cyan]")
        console.print(f"Лог: [cyan]{log_file}[/cyan]\n")


# ============================================================
# async-google-bidirectional
# ============================================================

@app.command(name="async-google-bidirectional")
def async_sync_google_bidirectional(
    ctx: typer.Context,
    sheet_id: Optional[str] = typer.Option(None, "--sheet-id", help="ID Google Sheets документа"),
    credentials: Optional[Path] = typer.Option(None, "--credentials", help="Путь к JSON файлу с credentials"),
    output: Path = typer.Option(Path("export.xlsx"), "--output", "-o", help="Путь для сохранения .xlsx файла"),
    sheets: Optional[str] = typer.Option(None, "--sheets", help="Список листов через запятую"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Путь к .env файлу",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="Параллельные запросы (default: 5)"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Максимум retry (default: 3)"),
    enable_circuit_breaker: bool = typer.Option(True, "--circuit-breaker/--no-circuit-breaker",
        help="Circuit breaker"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить медиа"),
    no_update_excel: bool = typer.Option(False, "--no-update-excel", help="Не писать ID в Excel"),
    no_upload_back: bool = typer.Option(False, "--no-upload-back",
        help="Не загружать обратно в Google Sheets"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять media при delete"),
    skip_existing_media: bool = typer.Option(False, "--skip-existing-media",
        help="Не прикреплять существующие featured_media при update"),
):
    """
    Async Google Sheets <-> WordPress <-> Google Sheets

    1. Скачать Google Sheets
    2. Async синхронизация с WordPress
    3. Загрузить обратно в Google Sheets
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "export.xlsx")

    console.print(f"\n[bold cyan]Google Sheets <-> WordPress <-> Google Sheets (Async)[/bold cyan]\n")

    with handle_command_errors():
        from ..utils.google_sheets import GoogleSheetsClient
        from .sync_commands import run_sync_async

        # ========== ШАГ 1: СКАЧИВАНИЕ ==========
        console.print("[bold yellow]ШАГ 1: Скачивание Google Sheets[/bold yellow]\n")

        final_sheet_id, credentials_path = _load_google_params(
            sheet_id, str(credentials) if credentials else None, env_file=env_file
        )

        gs_client = GoogleSheetsClient(credentials_path)
        info = gs_client.get_spreadsheet_info(final_sheet_id)
        console.print(f"[green]OK[/green] Документ: {info.get('name', 'Unknown')}")

        gs_client.download_as_excel(final_sheet_id, output)
        console.print(f"[bold green]OK Файл скачан![/bold green]\n")

        # ========== ШАГ 2: ASYNC СИНХРОНИЗАЦИЯ ==========
        console.print("[bold yellow]ШАГ 2: Async синхронизация с WordPress[/bold yellow]\n")

        domain, username, password = load_and_print_credentials(env_file=env_file)

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
            project_root=proj.root,
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

        console.print(f"\n[bold green]OK Синхронизация завершена![/bold green]\n")

        # ========== ШАГ 3: ЗАГРУЗКА ОБРАТНО ==========
        if not no_upload_back and not dry_run:
            console.print("[bold yellow]ШАГ 3: Загрузка обратно в Google Sheets[/bold yellow]\n")

            sheets_to_upload = [s.strip() for s in sheets.split(",")] if sheets else None

            gs_client.upload_excel_to_sheets(
                excel_path=output,
                spreadsheet_id=final_sheet_id,
                sheets_to_update=sheets_to_upload
            )

            console.print(f"[bold green]OK Загружено в Google Sheets![/bold green]\n")
        elif dry_run:
            console.print("[yellow]ШАГ 3 пропущен: DRY-RUN режим[/yellow]\n")
        else:
            console.print("[yellow]ШАГ 3 пропущен: --no-upload-back[/yellow]\n")

        console.print(f"[bold green]SUCCESS! Полный пайплайн завершен![/bold green]")
        console.print(f"Файл: [cyan]{output}[/cyan]")
        console.print(f"Лог: [cyan]{log_file}[/cyan]\n")
