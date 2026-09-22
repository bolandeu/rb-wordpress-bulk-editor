"""
Хелперы для разрешения путей с учётом --project контекста.
"""

from pathlib import Path
from typing import Optional

import typer

from .project import ProjectContext

# Импорт откладываем до вызова функций, чтобы избежать циклических импортов
def _console():
    from . import console
    return console


def _note(ctx: typer.Context, message: str):
    """Подсказка о разрешении пути — диагностика, значит stderr."""
    from .. import output

    options = output.get_options(ctx)
    if not options.json and not options.quiet:
        output.human.err_console.print(f"[dim]{message}[/dim]")


def _fail(ctx: typer.Context, message: str):
    """Ошибка аргументов: конверт (human или --json) и exit 2."""
    from .. import output
    from ..exceptions import ValidationError

    output.emit_error(ctx, ValidationError(message))


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

    Порядок поиска:
    1. explicit путь (абсолютный или существующий относительный)
    2. explicit путь относительно project root (если --project активен)
    3. Авто-поиск xlsx в папке проекта
    4. Ошибка с подсказкой
    """
    proj = get_project(ctx)

    if explicit is not None:
        # Абсолютный путь или существующий относительный
        if explicit.is_absolute() and explicit.exists():
            return explicit
        if explicit.exists():
            return explicit.resolve()
        # Пробуем относительно проекта
        if proj.active:
            project_path = proj.root / explicit
            if project_path.exists():
                _note(ctx, f"Excel файл: {project_path.name} (из проекта {proj.name})")
                return project_path
        # Файл не найден
        checked = f" Проверены пути: {explicit}, {proj.root / explicit}." if proj.active else ""
        _fail(ctx, f"Файл не найден: {explicit}.{checked}")

    # Авто-поиск в проекте
    if proj.active:
        found = proj.find_excel()
        if found:
            _note(ctx, f"Excel файл (авто): {found.name}")
            return found

    _fail(ctx, "Укажите --file или используйте --project с xlsx-файлом в папке проекта")


def resolve_logs_folder(ctx: typer.Context) -> Path:
    """Разрешить папку логов: проект > CWD/logs."""
    return get_project(ctx).logs_folder


def resolve_output(ctx: typer.Context, explicit: Path, default_name: str) -> Path:
    """Разрешить output: если проект активен — в папку проекта, иначе как указано."""
    proj = get_project(ctx)
    if proj.active:
        return proj.resolve_output(default_name)
    return explicit
