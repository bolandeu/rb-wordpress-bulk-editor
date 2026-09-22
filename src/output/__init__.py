"""
Вывод команд: человеку (rich) или агенту (JSON-конверт).

Команда не решает, куда и как печатать: она отдаёт payload в `emit_success`,
а `guard` превращает любое исключение в правильный exit-код и конверт ошибки.

Флаги `--json/--quiet/--verbose` работают и до подкоманды (`wp-bulk --json options`),
и после неё (`wp-bulk options --json`): глобальные ставит callback приложения,
локальные подмешивает `guard`.
"""

import functools
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import typer
from loguru import logger

from ..exceptions import CliError, WPBulkEditorError
from . import human
from . import json as json_output

__all__ = [
    "JsonOption",
    "OutputOptions",
    "QuietOption",
    "VerboseOption",
    "configure_logging",
    "emit_error",
    "emit_success",
    "get_options",
    "guard",
    "help_if_no_command",
    "human",
    "is_json",
    "set_global_options",
    "vecho",
]

# Локальные опции для листовых команд (глобальные живут в callback приложения).
JsonOption = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод.")
QuietOption = typer.Option(False, "--quiet", help="Только существенный вывод.")
VerboseOption = typer.Option(False, "--verbose", "-v", help="Подробная диагностика.")

_META_KEY = "wp_bulk.output"

# Имена параметров команды → поля OutputOptions.
_LOCAL_FLAGS = {
    "json_output": "json",
    "quiet": "quiet",
    "verbose": "verbose",
}


@dataclass
class OutputOptions:
    """Режим вывода текущего запуска."""
    json: bool = False
    quiet: bool = False
    verbose: bool = False


def get_options(ctx: Optional[typer.Context]) -> OutputOptions:
    """Опции вывода для этого запуска (хранятся в ctx.meta, общем для всей цепочки)."""
    if ctx is None:
        return OutputOptions()
    return ctx.meta.setdefault(_META_KEY, OutputOptions())


def set_global_options(ctx: typer.Context, json: bool, quiet: bool, verbose: bool) -> OutputOptions:
    """Записать глобальные флаги (вызывается из callback приложения)."""
    options = get_options(ctx)
    options.json = options.json or json
    options.quiet = options.quiet or quiet
    options.verbose = options.verbose or verbose
    return options


def is_json(ctx: Optional[typer.Context]) -> bool:
    return get_options(ctx).json


def configure_logging(options: OutputOptions) -> None:
    """
    Направить loguru в stderr: DEBUG при --verbose, ERROR при --quiet, иначе INFO.

    stdout остаётся чистым каналом данных — это условие работы `--json`.
    """
    level = "DEBUG" if options.verbose else ("ERROR" if options.quiet else "INFO")
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=level,
        colorize=True,
    )


def log_stream(ctx: Optional[typer.Context]):
    """Куда команде выводить консольный лог: в JSON-режиме только stderr."""
    return sys.stderr if get_options(ctx).json else sys.stdout


def vecho(ctx: Optional[typer.Context], message: str) -> None:
    """Прогресс для человека: только при --verbose и только в human-режиме."""
    options = get_options(ctx)
    if options.verbose and not options.json and not options.quiet:
        human.err_console.print(f"[dim]{message}[/dim]")


def emit_success(
    ctx: Optional[typer.Context],
    payload: Dict[str, Any],
    render_human: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Успешный результат: JSON-конверт или человеческий рендер."""
    options = get_options(ctx)
    if options.json:
        json_output.emit_success(payload)
    elif not options.quiet and render_human is not None:
        render_human(payload)


def emit_error(ctx: Optional[typer.Context], error: WPBulkEditorError) -> None:
    """Ошибка: JSON-конверт или строка в stderr. Всегда завершает команду."""
    if get_options(ctx).json:
        json_output.emit_error(error)
    else:
        human.error(error.message, error.action or "")
    raise typer.Exit(code=error.exit_code)


def help_if_no_command(ctx: typer.Context) -> None:
    """
    Голый `wp-bulk` (или группа без подкоманды) — это вопрос, а не ошибка:
    печатаем справку и выходим с кодом 0 вместо click'овского usage error (2).
    """
    if ctx.invoked_subcommand is not None:
        return
    text = ctx.get_help()
    if text:
        typer.echo(text)
    raise typer.Exit()


def guard(func: Callable) -> Callable:
    """
    Обернуть команду: любая ошибка уходит через `emit_error` с нужным exit-кодом.

    Ожидаемые ошибки (`WPBulkEditorError`) сохраняют свой code и exit-код.
    Всё остальное становится UNEXPECTED (exit 1) — агент под `--json` всё равно
    получает разбираемый конверт; traceback уходит в stderr только при --verbose.

    Заодно подмешивает локальные `--json/--quiet/--verbose` в опции вывода,
    чтобы `wp-bulk options --json` работал так же, как `wp-bulk --json options`.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any):
        ctx = kwargs.get("ctx")
        if ctx is None and args and isinstance(args[0], typer.Context):
            ctx = args[0]

        options = get_options(ctx)
        for flag_name, option_name in _LOCAL_FLAGS.items():
            if kwargs.get(flag_name):
                setattr(options, option_name, True)
        if ctx is not None:
            configure_logging(options)

        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise  # уже контролируемый выход
        except WPBulkEditorError as e:
            if options.verbose:
                logger.opt(exception=True).debug("Ошибка команды")
            emit_error(ctx, e)
        except Exception as e:
            if options.verbose:
                human.err_console.print_exception()
            emit_error(
                ctx,
                CliError(
                    f"{type(e).__name__}: {e}",
                    action="Повторите с --verbose, чтобы увидеть traceback.",
                ),
            )

    return wrapper
