"""
CLI интерфейс для WordPress Bulk Editor

Модульная структура:
- sync_commands.py    - sync, async (синхронизация Excel -> WordPress)
- fetch_commands.py   - fetch, fetch-media (загрузка WordPress -> Excel)
- export_commands.py  - export, export-media (экспорт контента/медиа в файлы)
- tool_commands.py    - validate, options, bulk-delete, counter, llm, reshape, taxonomies
- doctor_commands.py  - doctor (диагностика окружения)
- image_commands.py   - image-crop, image-compress, image-convert
- media_commands.py   - media-upload (файлы с диска → медиатека, id вложений)
"""

import sys
from typing import Optional

import typer

from .project import PACKAGE_NAME, check_cli_compat, installed_version, resolve_project
from .. import output
from ..data import read_skill_doc
from ..exceptions import CliTooOld, ConfigurationError

app = typer.Typer(
    name="wp-bulk-editor",
    help="WordPress Bulk Editor - Excel <-> WordPress sync",
    # Голый `wp-bulk` печатает справку и выходит с 0 (см. app_callback),
    # а не usage error с кодом 2, как делает click по умолчанию.
    no_args_is_help=False,
)
console = output.human.console

# Команды, которым проверка WP_BULK_MIN_VERSION не должна мешать запуститься:
# doctor обязан уметь диагностировать именно несовместимую версию.
_SKIP_COMPAT_COMMANDS = {"doctor"}


def _print_skill_doc(value: bool) -> None:
    """--ai: напечатать бандленный SKILL.md (для агента без доступа к ФС) и выйти."""
    if not value:
        return
    typer.echo(read_skill_doc())
    raise typer.Exit()


def _print_version(value: bool) -> None:
    """--version: напечатать `<имя> <версия> — <описание>` и выйти."""
    if not value:
        return
    version = installed_version() or "0.0.0-src (запущено из исходников, не установлен как пакет)"
    typer.echo(f"{PACKAGE_NAME} {version} — WordPress Bulk Editor: Excel <-> WordPress sync")
    raise typer.Exit()


@app.callback(invoke_without_command=True)
def app_callback(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(
        None,
        "--project", "-P",
        help="Путь к директории проекта или её имя внутри projects/ (по умолчанию — "
        "автопоиск .env вверх от текущей директории)",
        envvar="WP_PROJECT",
    ),
    json_output: bool = typer.Option(False, "--json", help="Машиночитаемый JSON-вывод."),
    quiet: bool = typer.Option(False, "--quiet", help="Только существенный вывод."),
    verbose: bool = typer.Option(False, "--verbose", help="Подробная диагностика."),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Никогда не спрашивать; вместо вопроса — ошибка.",
    ),
    ai: bool = typer.Option(
        False,
        "--ai",
        help="Напечатать бандленный SKILL.md (соглашения CLI) и выйти.",
        is_eager=True,
        callback=_print_skill_doc,
    ),
    version: bool = typer.Option(
        False,
        "--version",
        help="Напечатать версию и выйти.",
        is_eager=True,
        callback=_print_version,
    ),
):
    """WordPress Bulk Editor - Excel <-> WordPress sync"""
    options = output.set_global_options(
        ctx, json=json_output, quiet=quiet, verbose=verbose, non_interactive=non_interactive
    )
    output.configure_logging(options)

    try:
        project_ctx = resolve_project(project)
    except FileNotFoundError as e:
        output.emit_error(ctx, ConfigurationError(str(e)))
        return

    ctx.ensure_object(dict)
    ctx.obj = project_ctx

    if project_ctx.active and ctx.invoked_subcommand not in _SKIP_COMPAT_COMMANDS:
        try:
            check_cli_compat(project_ctx)
        except CliTooOld as e:
            # Локальный `--json` (после имени команды) click ещё не разбирал, а
            # конверт агент ждёт в обеих формах — здесь смотрим argv напрямую.
            if "--json" in sys.argv[1:]:
                options.json = True
            output.emit_error(ctx, e)
            return

    if project_ctx.active and not options.json and not options.quiet:
        # Диагностика — в stderr, чтобы не смешиваться с данными в stdout.
        output.human.err_console.print(f"[dim]Проект: {project_ctx.name} ({project_ctx.root})[/dim]")

    output.help_if_no_command(ctx)


# Регистрируем команды из подмодулей
# Импорты размещены после создания app, чтобы подмодули могли импортировать app
from . import sync_commands  # noqa: E402, F401
from . import fetch_commands  # noqa: E402, F401
from . import update_commands  # noqa: E402, F401
from . import export_commands  # noqa: E402, F401
from . import tool_commands  # noqa: E402, F401
from . import doctor_commands  # noqa: E402, F401
from . import image_commands  # noqa: E402, F401
from . import vlook_commands  # noqa: E402, F401
from . import media_commands  # noqa: E402, F401


def main():
    """Точка входа"""
    app()
