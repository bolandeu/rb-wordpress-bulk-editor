"""
CLI интерфейс для WordPress Bulk Editor

Модульная структура:
- sync_commands.py    - sync, async (синхронизация Excel -> WordPress)
- fetch_commands.py   - fetch, fetch-media (загрузка WordPress -> Excel)
- export_commands.py  - export, export-media (экспорт контента/медиа в файлы)
- google_commands.py  - download-google-sheet, upload-to-google-sheets,
                        sync-from-google, sync-google-bidirectional,
                        async-from-google, async-google-bidirectional
- tool_commands.py    - validate, options, bulk-delete, counter, llm, reshape
- image_commands.py   - image-crop, image-compress, image-convert
"""

from typing import Optional

import typer
from rich.console import Console

from .project import resolve_project

app = typer.Typer(
    name="wp-bulk-editor",
    help="WordPress Bulk Editor - Excel <-> WordPress sync"
)
console = Console()


@app.callback()
def app_callback(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(
        None,
        "--project", "-P",
        help="Имя проекта (загружает .env и пути из projects/<name>/)",
        envvar="WP_PROJECT",
    ),
):
    """WordPress Bulk Editor - Excel <-> WordPress sync"""
    try:
        project_ctx = resolve_project(project)
    except FileNotFoundError as e:
        console.print(f"[bold red]ERROR: {e}[/bold red]")
        raise typer.Exit(1)

    ctx.ensure_object(dict)
    ctx.obj = project_ctx

    if project_ctx.active:
        console.print(f"[dim]Проект: {project_ctx.name} ({project_ctx.root})[/dim]")


# Регистрируем команды из подмодулей
# Импорты размещены после создания app, чтобы подмодули могли импортировать app
from . import sync_commands  # noqa: E402, F401
from . import fetch_commands  # noqa: E402, F401
from . import export_commands  # noqa: E402, F401
from . import google_commands  # noqa: E402, F401
from . import tool_commands  # noqa: E402, F401
from . import image_commands  # noqa: E402, F401
from . import vlook_commands  # noqa: E402, F401


def main():
    """Точка входа"""
    app()
