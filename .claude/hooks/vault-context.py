"""
Vault Context Hook (SessionStart)
Загружает контекст текущего проекта из Obsidian при старте сессии.
Также показывает SESSION_LOG за сегодня — чтобы при повторном входе
в проект Claude знал что уже сделано.

Структура: ~/Projects/Obsidian/projects/<project-name>/
"""

import os
import sys
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import VAULT_PATH, DAILY_PATH, get_project_name, get_project_vault_path


def get_today_session_log():
    """Возвращает записи SESSION_LOG.md за сегодня."""
    path = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, "CLAUDE.md")):
            break
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent

    log_path = os.path.join(path, "SESSION_LOG.md")
    if not os.path.exists(log_path):
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Ищем заголовок сегодняшнего дня и берём все строки после него
    today_lines = []
    in_today = False
    for line in lines:
        if line.strip() == f"## {today}":
            in_today = True
            continue
        elif line.startswith("## ") and in_today:
            break
        elif in_today:
            today_lines.append(line.rstrip())

    entries = [l for l in today_lines if l.strip()]
    return entries if entries else None


def main():
    project_name = get_project_name()
    sections = []

    # SESSION_LOG за сегодня
    today_entries = get_today_session_log()
    if today_entries:
        sections.append(f"Сегодня в проекте '{project_name}' уже было:")
        sections.extend(today_entries)

    # Obsidian заметки
    project_path = get_project_vault_path(project_name)
    if os.path.isdir(project_path):
        notes = sorted(glob.glob(os.path.join(project_path, "*.md")))
        if notes:
            note_parts = []
            total = 0
            for note in notes:
                with open(note, "r", encoding="utf-8") as f:
                    content = f.read()
                name = os.path.basename(note)
                note_parts.append(f"### {name}\n{content}")
                total += len(content)
                if total > 3000:
                    break
            sections.append(f"\nКонтекст проекта '{project_name}' из Obsidian:")
            sections.append("\n---\n".join(note_parts))

    # Всегда показывать абсолютные пути vault — чтобы LLM не гадал
    project_path = get_project_vault_path(project_name)
    sections.append(f"\n[vault paths] project: {project_path} | daily: {DAILY_PATH}")

    print("\n".join(sections))


if __name__ == "__main__":
    main()
