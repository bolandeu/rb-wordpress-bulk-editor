"""
Session End Hook (Stop)
Напоминает о незакоммиченных изменениях и непереданных логах.
"""

import subprocess
import os
import sys


def find_project_root():
    path = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, "CLAUDE.md")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return os.getcwd()
        path = parent


def main():
    root = find_project_root()
    messages = []

    # Проверяем незакоммиченные изменения
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=root
        )
        if result.stdout.strip():
            changed = len(result.stdout.strip().splitlines())
            messages.append(f"Незакоммиченные изменения: {changed} файлов")
    except Exception:
        pass

    # Проверяем SESSION_LOG.md
    log_path = os.path.join(root, "SESSION_LOG.md")
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            entries = len([l for l in content.splitlines() if l.startswith("- [")])
            # После 18:00 — усиленное напоминание
            from datetime import datetime
            hour = datetime.now().hour
            if hour >= 18:
                messages.append(f"SESSION_LOG.md: {entries} записей. Конец дня — запусти /rb-close-day для компиляции wiki")
            else:
                messages.append(f"SESSION_LOG.md не перенесён: {entries} записей. Запусти /rb-close-day")

    if messages:
        print("\n".join(messages))


if __name__ == "__main__":
    main()
