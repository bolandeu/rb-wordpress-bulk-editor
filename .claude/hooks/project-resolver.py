"""
Project Resolver — общий модуль для поиска проекта.

Используется commands и hooks для определения корня проекта.

Логика поиска:
1. Если передано имя — ищет по имени в ~/Projects/*/ и ~/Projects/*/*/
2. Если нет — ищет CLAUDE.md вверх по дереву от cwd

Использование в commands (через инструкцию агенту):
  Для определения проекта используй project-resolver:
  - Без аргумента: ищи CLAUDE.md вверх от cwd
  - С именем: ищи папку в ~/Projects/*/ и ~/Projects/*/*/
  - Проект найден = папка с CLAUDE.md

Использование в hooks:
  from project_resolver import find_project
  root = find_project()        # по cwd
  root = find_project("doit")  # по имени
"""

import os
import glob

PROJECTS_ROOT = os.path.expanduser("~/Projects")
MAX_DEPTH = 4  # ~/Projects/group/subgroup/project/


def find_project(name=None):
    """
    Найти корень проекта.

    Args:
        name: имя проекта (папки) или путь. Если None — ищет вверх от cwd.

    Returns:
        Абсолютный путь к корню проекта или None.
    """
    if name:
        # Если передан полный путь
        if os.path.isabs(name) and os.path.isdir(name):
            if os.path.exists(os.path.join(name, "CLAUDE.md")):
                return name
            return None
        return _find_by_name(name)
    return _find_by_cwd()


def _find_by_cwd():
    """Ищет CLAUDE.md вверх по дереву от текущей директории."""
    path = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, "CLAUDE.md")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _find_by_name(name):
    """Ищет проект по имени в ~/Projects/ рекурсивно (до MAX_DEPTH)."""
    name_lower = name.lower()

    # Точное совпадение имени папки
    for candidate in _walk_projects():
        if os.path.basename(candidate).lower() == name_lower:
            return candidate

    # Нечёткий — имя содержится в названии папки
    for candidate in _walk_projects():
        if name_lower in os.path.basename(candidate).lower():
            return candidate

    return None


def _walk_projects():
    """Обход ~/Projects/ до MAX_DEPTH, возвращает папки с CLAUDE.md."""
    for depth in range(1, MAX_DEPTH + 1):
        pattern = os.path.join(PROJECTS_ROOT, *["*"] * depth)
        for candidate in sorted(glob.glob(pattern)):
            if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "CLAUDE.md")):
                yield candidate


def list_projects():
    """Список всех проектов (папок с CLAUDE.md)."""
    seen = set()
    projects = []
    for candidate in _walk_projects():
        if candidate not in seen:
            seen.add(candidate)
            projects.append(candidate)
    return projects


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = find_project(sys.argv[1])
    else:
        result = find_project()

    if result:
        print(result)
    else:
        print("Проект не найден", file=sys.stderr)
        sys.exit(1)
