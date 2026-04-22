"""
Docs Reminder Hook (Post-Tool-Use)
Напоминает обновить документацию при изменении ключевых файлов.

Если изменился файл с роутами, схемой БД или архитектурой —
выводит напоминание проверить соответствующий docs/*.md.
"""

import json
import os
import sys
import re

sys.path.insert(0, os.path.dirname(__file__))
from config import safe_read_stdin

# Паттерны файлов → какой doc проверить
TRIGGERS = [
    # API / роуты
    {
        "files": [r"route", r"router", r"api[./]", r"endpoint", r"controller", r"server\.(js|ts|py)"],
        "doc": "docs/API.md",
        "msg": "Изменены роуты/API — проверь docs/API.md"
    },
    # Модели / схема БД / миграции
    {
        "files": [r"model", r"schema", r"migrat", r"db\.(js|ts|py)", r"database", r"\.sql$"],
        "doc": "docs/DATA-MODEL.md",
        "msg": "Изменена схема данных — проверь docs/DATA-MODEL.md"
    },
    # Архитектура — новые модули верхнего уровня
    {
        "files": [r"^src/[^/]+/__init__\.py$", r"^src/[^/]+/index\.(js|ts)$"],
        "doc": "docs/ARCHITECTURE.md",
        "msg": "Добавлен/изменён модуль — проверь docs/ARCHITECTURE.md"
    },
    # Деплой
    {
        "files": [r"Dockerfile", r"docker-compose", r"\.github/workflows", r"deploy", r"Makefile"],
        "doc": "docs/DEPLOY.md",
        "msg": "Изменена инфраструктура — проверь docs/DEPLOY.md"
    },
]

SKIP = [".claude/", "node_modules/", ".git/", "docs/", "SESSION_LOG"]


def main():
    input_data = safe_read_stdin("docs-reminder")
    if input_data is None:
        return
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return

    file_path = tool_input.get("file_path", "")

    for skip in SKIP:
        if skip in file_path:
            return

    for trigger in TRIGGERS:
        for pattern in trigger["files"]:
            if re.search(pattern, file_path, re.IGNORECASE):
                print(f"📝 {trigger['msg']}")
                return


if __name__ == "__main__":
    main()
