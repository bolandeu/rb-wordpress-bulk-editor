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
from typing import Any, Callable, Dict, NoReturn, Optional

import typer
from loguru import logger

from ..exceptions import Aborted, CliError, ValidationError, WPBulkEditorError
from . import human
from . import json as json_output

__all__ = [
    "ConfirmOption",
    "JsonOption",
    "NonInteractiveOption",
    "OutputOptions",
    "QuietOption",
    "VerboseOption",
    "configure_logging",
    "emit_error",
    "emit_report",
    "emit_success",
    "get_options",
    "group",
    "guard",
    "help_if_no_command",
    "human",
    "is_interactive",
    "is_json",
    "require_confirmation",
    "set_global_options",
    "vecho",
]

# Локальные опции для листовых команд (глобальные живут в callback приложения).
JsonOption = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод.")
QuietOption = typer.Option(False, "--quiet", help="Только существенный вывод.")
VerboseOption = typer.Option(False, "--verbose", "-v", help="Подробная диагностика.")
NonInteractiveOption = typer.Option(False, "--non-interactive", help="Никогда не спрашивать; вместо вопроса — ошибка.")
ConfirmOption = typer.Option(False, "--confirm", help="Подтвердить опасную операцию без вопроса.")

_META_KEY = "wp_bulk.output"

# Имя команды: `{cli}` в `Example:`-строках docstring'ов (= --help) заменяется им.
CLI_NAME = "wp-bulk"

# Имена параметров команды → поля OutputOptions.
_LOCAL_FLAGS = {
    "json_output": "json",
    "quiet": "quiet",
    "verbose": "verbose",
    "non_interactive": "non_interactive",
}


@dataclass
class OutputOptions:
    """Режим вывода текущего запуска."""

    json: bool = False
    quiet: bool = False
    verbose: bool = False
    non_interactive: bool = False


def get_options(ctx: Optional[typer.Context]) -> OutputOptions:
    """Опции вывода для этого запуска (хранятся в ctx.meta, общем для всей цепочки)."""
    if ctx is None:
        return OutputOptions()
    return ctx.meta.setdefault(_META_KEY, OutputOptions())


def set_global_options(
    ctx: typer.Context,
    json: bool,
    quiet: bool,
    verbose: bool,
    non_interactive: bool = False,
) -> OutputOptions:
    """Записать глобальные флаги (вызывается из callback приложения)."""
    options = get_options(ctx)
    options.json = options.json or json
    options.quiet = options.quiet or quiet
    options.verbose = options.verbose or verbose
    options.non_interactive = options.non_interactive or non_interactive
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
    """Куда команде выводить консольный лог: при --json/--quiet только stderr."""
    options = get_options(ctx)
    return sys.stderr if options.json or options.quiet else sys.stdout


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


def emit_report(
    ctx: Optional[typer.Context],
    payload: Dict[str, Any],
    render_human: Callable[[Dict[str, Any]], None],
    problem: Optional[WPBulkEditorError],
) -> None:
    """
    Отчёт, который остаётся данными, даже если нашёл проблему (`auth status`).

    Без проблемы — ровно `emit_success`. С проблемой — весь payload плюс
    `ok: false` и стандартный объект `error`, выход с exit-кодом проблемы.
    """
    if problem is None:
        emit_success(ctx, payload, render_human)
        return
    options = get_options(ctx)
    if options.json:
        json_output.emit_report(payload, problem)
    else:
        if not options.quiet:
            render_human(payload)
        human.error(problem.message, problem.action or "")
    raise typer.Exit(code=problem.exit_code)


def emit_error(ctx: Optional[typer.Context], error: WPBulkEditorError) -> NoReturn:
    """Ошибка: JSON-конверт или строка в stderr. Всегда завершает команду."""
    if get_options(ctx).json:
        json_output.emit_error(error)
    else:
        human.error(error.message, error.action or "")
    raise typer.Exit(code=error.exit_code)


def is_interactive(ctx: Optional[typer.Context]) -> bool:
    """
    Можно ли задать пользователю вопрос.

    Нельзя при `--non-interactive`, в JSON-режиме (вопрос сломал бы конверт)
    и когда stdin не терминал (пайп, CI, вызов агентом): там вопрос — это зависание.
    """
    options = get_options(ctx)
    if options.non_interactive or options.json:
        return False
    return sys.stdin is not None and sys.stdin.isatty()


def require_confirmation(
    ctx: Optional[typer.Context],
    description: str,
    confirm: bool = False,
) -> None:
    """
    Гейт перед необратимой операцией. Вызывать после проверки --dry-run.

    `--confirm` пропускает вопрос. Человеку у терминала задаётся вопрос;
    отказ — `Aborted` (exit 1). Без терминала (или при --non-interactive/--json)
    без `--confirm` — `INVALID_ARGUMENTS` (exit 2), а не зависший prompt:
    агент не должен запускать необратимое случайно.
    """
    if confirm:
        return

    if not is_interactive(ctx):
        raise ValidationError(
            f"Операция необратима и требует подтверждения: {description}",
            action="Повторите с --confirm (или уберите --non-interactive), либо посмотрите план с --dry-run.",
        )

    if not typer.confirm(description):
        raise Aborted("Отменено пользователем")


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


def group(help_text: str) -> typer.Typer:
    """Группа команд (`wp-bulk auth ...`): голая группа печатает справку с exit 0."""
    app = typer.Typer(help=help_text)

    @app.callback(invoke_without_command=True)
    def _default(ctx: typer.Context) -> None:
        help_if_no_command(ctx)

    return app


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

    # Docstring — это --help; `{cli}` в строках `Example:` становится именем команды.
    wrapper.__doc__ = (func.__doc__ or "").replace("{cli}", CLI_NAME) or None
    return wrapper
