"""
Общие хелперы для CLI команд: логирование, обработка ошибок, credentials.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List

import typer
from loguru import logger

from . import console
from .helpers import resolve_logs_folder
from ..config import LoggingConfig
from ..exceptions import WPBulkEditorError
from ..utils.logger import setup_logger


def setup_command_logging(ctx: typer.Context, command_name: str, verbose: bool = False) -> Path:
    """Стандартная настройка логирования для команды."""
    logs_folder = resolve_logs_folder(ctx)
    logs_folder.mkdir(parents=True, exist_ok=True)
    logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")
    return setup_logger(
        logs_folder=logs_folder,
        config=logging_config,
        command_name=command_name,
    )


@contextmanager
def handle_command_errors():
    """
    Context manager для единообразной обработки ошибок в CLI командах.

    Использование:
        with handle_command_errors():
            ... бизнес-логика команды ...
    """
    try:
        yield
    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


def load_and_print_credentials(
    env_file: Optional[Path] = None,
    domain: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
):
    """Загрузить credentials и вывести в консоль."""
    from ..utils.auth import load_credentials_from_env

    console.print("[yellow]Загрузка credentials...[/yellow]")
    domain, username, password = load_credentials_from_env(
        env_file=env_file,
        domain_override=domain,
        username_override=username,
        password_override=password,
    )
    console.print(f"[green]OK[/green] WordPress: {domain}")
    console.print(f"[green]OK[/green] User: {username}\n")
    return domain, username, password


def parse_comma_list(value: Optional[str]) -> Optional[List[str]]:
    """Разобрать строку 'a, b, c' в список ['a', 'b', 'c']. None → None."""
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]
