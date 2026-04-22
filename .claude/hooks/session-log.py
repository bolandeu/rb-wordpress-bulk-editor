"""
Session Log Hook (Post-Tool-Use)
Записывает каждое изменение файла в SESSION_LOG.md

Когда Claude создаёт или редактирует файл,
этот хук автоматически добавляет запись в лог.
Потом /close-day собирает эти логи в Obsidian Daily Note.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import safe_read_stdin

SKIP = ["SESSION_LOG.md", ".claude/", "node_modules/", ".git/"]


def main():
    input_data = safe_read_stdin("session-log")
    if input_data is None:
        return
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Только для Write/Edit
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return

    file_path = tool_input.get("file_path", "")

    # Пропускаем служебные файлы
    for skip in SKIP:
        if skip in file_path:
            return

    # Находим корень проекта
    project_root = os.path.dirname(os.path.abspath(file_path))
    while not os.path.exists(os.path.join(project_root, "CLAUDE.md")):
        parent = os.path.dirname(project_root)
        if parent == project_root:
            return
        project_root = parent

    # Пишем в SESSION_LOG.md
    log_path = os.path.join(project_root, "SESSION_LOG.md")
    timestamp = datetime.now().strftime("%H:%M")
    filename = os.path.basename(file_path)
    action = "created" if tool_name == "Write" else "edited"

    # Относительный путь для читаемости
    try:
        rel_path = os.path.relpath(file_path, project_root).replace("\\", "/")
    except ValueError:
        rel_path = filename

    entry = f"- [{timestamp}] {action}: `{rel_path}`\n"

    # Добавляем заголовок дня, если его ещё нет
    date_header = f"## {datetime.now().strftime('%Y-%m-%d')}\n"
    existing = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            existing = f.read()

    with open(log_path, "a", encoding="utf-8") as f:
        # Если файл пуст или нет заголовка текущего дня — добавляем
        if date_header.strip() not in existing:
            if existing and not existing.endswith("\n\n"):
                f.write("\n")
            f.write(date_header)
        f.write(entry)


if __name__ == "__main__":
    main()
