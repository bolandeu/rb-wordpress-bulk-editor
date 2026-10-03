"""
Хелперы для разрешения путей с учётом --project контекста.
"""

from pathlib import Path
from typing import NoReturn, Optional

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


def _fail(ctx: typer.Context, message: str) -> NoReturn:
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


def _require_file(ctx: typer.Context, explicit: Optional[Path], finder, label: str, hint: str) -> Path:
    """
    Общая логика require_*_file: явный путь > путь от корня проекта > авто-поиск.

    Args:
        finder: функция ProjectContext -> Optional[Path] для авто-поиска
        label: как называть файл в подсказках
        hint: текст ошибки, если ничего не нашлось
    """
    from ..exceptions import ValidationError

    proj = get_project(ctx)

    if explicit is not None:
        # Абсолютный путь или существующий относительный
        if explicit.is_absolute() and explicit.exists():
            return explicit
        if explicit.exists():
            return explicit.resolve()
        # Пробуем относительно проекта
        if proj.active and proj.root is not None:
            project_path = proj.root / explicit
            if project_path.exists():
                _note(ctx, f"{label}: {project_path.name} (из проекта {proj.name})")
                return project_path
        # Файл не найден
        checked = f" Проверены пути: {explicit}, {proj.root / explicit}." if proj.root is not None else ""
        _fail(ctx, f"Файл не найден: {explicit}.{checked}")

    # Авто-поиск в проекте
    if proj.active:
        try:
            found = finder(proj)
        except ValidationError as e:
            # Неоднозначность (несколько форматов данных) — обычная ошибка аргументов.
            _fail(ctx, e.message)
        else:
            if found:
                _note(ctx, f"{label} (авто): {found.name}")
                return found

    _fail(ctx, hint)


def require_excel_file(ctx: typer.Context, explicit: Optional[Path]) -> Path:
    """
    Разрешить Excel файл с обязательным результатом.

    Только для команд, которые по своей природе работают с Excel
    (fetch-media, export и т.п.).
    """
    return _require_file(
        ctx,
        explicit,
        lambda proj: proj.find_excel(),
        "Excel файл",
        "Укажите --file или используйте --project с xlsx-файлом в папке проекта",
    )


def require_data_file(ctx: typer.Context, explicit: Optional[Path]) -> Path:
    """
    Разрешить файл данных (xlsx или json) с обязательным результатом.

    Для команд, умеющих оба источника (sync/async): конкретный источник
    выбирается дальше по расширению файла.
    """
    return _require_file(
        ctx,
        explicit,
        lambda proj: proj.find_data_file(),
        "Файл данных",
        "Укажите --file или используйте --project с xlsx/json-файлом в папке проекта",
    )


def resolve_logs_folder(ctx: typer.Context) -> Path:
    """Разрешить папку логов: <проект>/logs, без проекта — каталог логов пользователя (platformdirs)."""
    return get_project(ctx).logs_folder


def resolve_output(ctx: typer.Context, explicit: Path, default_name: str) -> Path:
    """Разрешить output: если проект активен — в папку проекта, иначе как указано."""
    proj = get_project(ctx)
    if proj.active:
        return proj.resolve_output(default_name)
    return explicit
