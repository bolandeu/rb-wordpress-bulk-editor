"""
Compact Context Hook (Notification)
Повторно внедряет ключевой контекст при компактизации.

Когда контекст сжимается (автоматически или через /compact),
Claude может потерять нить. Этот хук напоминает о текущей задаче
и изменённых файлах, чтобы работа продолжилась без потери фокуса.
"""

import json
import sys
import os
from datetime import datetime


def get_session_log_tail(project_root, max_lines=20):
    """Читает последние записи из SESSION_LOG.md."""
    log_path = os.path.join(project_root, "SESSION_LOG.md")
    if not os.path.exists(log_path):
        return None

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Берём последние max_lines строк (обычно это текущий день)
    recent = [l.rstrip() for l in lines[-max_lines:] if l.strip()]
    return "\n".join(recent) if recent else None


def get_git_changes(project_root):
    """Получает список изменённых файлов через git."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        files = set()
        for output in [result.stdout, staged.stdout]:
            files.update(f.strip() for f in output.splitlines() if f.strip())
        return sorted(files) if files else None
    except Exception:
        return None


def find_project_root():
    """Находит корень проекта по CLAUDE.md."""
    cwd = os.getcwd()
    current = cwd
    while True:
        if os.path.exists(os.path.join(current, "CLAUDE.md")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return cwd
        current = parent


def main():
    input_data = json.loads(sys.stdin.read())

    project_root = find_project_root()
    parts = []

    parts.append("=== КОНТЕКСТ ПОСЛЕ КОМПАКТИЗАЦИИ ===")

    # Изменённые файлы из git
    git_files = get_git_changes(project_root)
    if git_files:
        parts.append(f"\nИзменённые файлы ({len(git_files)}):")
        for f in git_files:
            parts.append(f"  - {f}")

    # Последние действия из SESSION_LOG.md
    session_tail = get_session_log_tail(project_root)
    if session_tail:
        parts.append(f"\nПоследние действия сессии:")
        parts.append(session_tail)

    if len(parts) > 1:
        print("\n".join(parts))


if __name__ == "__main__":
    main()
