"""
Хелперы для разрешения путей с учётом --project контекста.
"""

from pathlib import Path
from typing import Optional

import typer

from .project import ProjectContext

# Импорт console откладываем до вызова функций, чтобы избежать циклических импортов
def _console():
    from . import console
    return console


def get_project(ctx: typer.Context) -> ProjectContext:
    """Получить ProjectContext из typer контекста."""
    obj = ctx.obj
    if isinstance(obj, ProjectContext):
        return obj
    return ProjectContext()


def resolve_env_file(ctx: typer.Context, explicit: Optional[Path]) -> Optional[Path]:
    """Разрешить .env: явный путь > проект > None (глобальный .env)."""
    if explicit:
        return explicit
    return get_project(ctx).env_file


def resolve_excel_file(ctx: typer.Context, explicit: Optional[Path]) -> Optional[Path]:
    """Разрешить Excel файл: явный путь > авто-поиск в проекте > None."""
    if explicit:
        return explicit
    return get_project(ctx).find_excel()


def require_excel_file(ctx: typer.Context, explicit: Optional[Path]) -> Path:
    """
    Разрешить Excel файл с обязательным результатом.
    explicit > авто-поиск в проекте > ошибка с подсказкой.
    """
    file = resolve_excel_file(ctx, explicit)
    if file is None:
        _console().print(
            "[red]ERROR: Укажите --file или используйте --project с xlsx-файлом в папке проекта[/red]"
        )
        raise typer.Exit(code=1)
    if explicit is None:
        _console().print(f"[dim]Excel файл (авто): {file.name}[/dim]")
    return file


def resolve_logs_folder(ctx: typer.Context) -> Path:
    """Разрешить папку логов: проект > CWD/logs."""
    return get_project(ctx).logs_folder


def resolve_output(ctx: typer.Context, explicit: Path, default_name: str) -> Path:
    """Разрешить output: если проект активен — в папку проекта, иначе как указано."""
    proj = get_project(ctx)
    if proj.active:
        return proj.resolve_output(default_name)
    return explicit
