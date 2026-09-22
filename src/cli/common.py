"""
Общие хелперы для CLI команд: логирование, обработка ошибок, credentials.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List

import typer
from loguru import logger

from .helpers import resolve_logs_folder
from .. import output
from ..config import LoggingConfig
from ..exceptions import CliError, WPBulkEditorError
from ..utils.logger import setup_logger


def setup_command_logging(
    ctx: typer.Context,
    command_name: str,
    verbose: bool = False,
) -> Path:
    """Стандартная настройка логирования для команды."""
    logs_folder = resolve_logs_folder(ctx)
    logs_folder.mkdir(parents=True, exist_ok=True)
    options = output.get_options(ctx)
    logging_config = LoggingConfig(level="DEBUG" if (verbose or options.verbose) else "INFO")
    return setup_logger(
        logs_folder=logs_folder,
        config=logging_config,
        command_name=command_name,
        console_stream=output.log_stream(ctx),
    )


@contextmanager
def handle_command_errors(ctx: Optional[typer.Context] = None):
    """
    Обработка ошибок команды: нужный exit-код и конверт ошибки (human или --json).

    То же самое, что декоратор `output.guard`, но в виде context manager —
    для команд, которые ещё не переведены на декоратор.

    Использование:
        with handle_command_errors(ctx):
            ... бизнес-логика команды ...
    """
    try:
        yield
    except (typer.Exit, typer.Abort):
        raise
    except WPBulkEditorError as e:
        output.emit_error(ctx, e)
    except Exception as e:
        logger.exception("Неожиданная ошибка")
        output.emit_error(
            ctx,
            CliError(
                f"{type(e).__name__}: {e}",
                action="Повторите с --verbose, чтобы увидеть traceback.",
            ),
        )


def load_and_print_credentials(
    env_file: Optional[Path] = None,
    domain: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    ctx: Optional[typer.Context] = None,
):
    """
    Загрузить credentials (CLI → окружение → keychain → legacy .env) и показать их.

    Сообщения идут в stderr: это диагностика, а не результат команды.
    """
    from ..utils.auth import load_credentials_from_env

    options = output.get_options(ctx)
    err = output.human.err_console

    if not options.json and not options.quiet:
        err.print("[yellow]Загрузка credentials...[/yellow]")
    domain, username, password = load_credentials_from_env(
        env_file=env_file,
        domain_override=domain,
        username_override=username,
        password_override=password,
    )
    if not options.json and not options.quiet:
        err.print(f"[green]OK[/green] WordPress: {domain}")
        err.print(f"[green]OK[/green] User: {username}\n")
    return domain, username, password


def parse_comma_list(value: Optional[str]) -> Optional[List[str]]:
    """Разобрать строку 'a, b, c' в список ['a', 'b', 'c']. None → None."""
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]
