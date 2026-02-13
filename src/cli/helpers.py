"""
Хелперы для разрешения путей с учётом --project контекста.
"""

from pathlib import Path
from typing import Optional

import typer

from .project import ProjectContext


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


def resolve_logs_folder(ctx: typer.Context) -> Path:
    """Разрешить папку логов: проект > CWD/logs."""
    return get_project(ctx).logs_folder


def resolve_output(ctx: typer.Context, explicit: Path, default_name: str) -> Path:
    """Разрешить output: если проект активен — в папку проекта, иначе как указано."""
    proj = get_project(ctx)
    if proj.active:
        return proj.resolve_output(default_name)
    return explicit
