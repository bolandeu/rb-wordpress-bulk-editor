"""
Safety Guard Hook (Pre-Tool-Use)
Блокирует опасные команды в терминале.

Если Claude пытается выполнить rm -rf, force push,
или запустить скрипт из интернета — хук его остановит.
"""

import json
import os
import sys
import re

sys.path.insert(0, os.path.dirname(__file__))
from config import safe_read_stdin

# Опасные паттерны команд
BLOCK_PATTERNS = [
    r'rm\s+-rf\s+/',            # Удаление корневых папок
    r'rm\s+-rf\s+~',            # Удаление домашней папки
    r'git\s+push\s+--force',    # Force push (потеря истории)
    r'git\s+push\s+-f\s',       # Force push (короткая форма)
    r'git\s+reset\s+--hard',    # Сброс всех изменений
    r'git\s+clean\s+-f',        # Удаление untracked файлов
    r'curl.*\|\s*bash',         # Скачать и сразу выполнить (опасно!)
    r'wget.*\|\s*bash',         # То же через wget
    r'npm\s+publish',           # Публикация пакета (необратимо)
    r'DROP\s+TABLE',            # Удаление таблицы в БД
    r'DROP\s+DATABASE',         # Удаление базы данных
]


def main():
    # Читаем что Claude хочет выполнить
    input_data = safe_read_stdin("safety-guard")
    if input_data is None:
        return
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Проверяем только Bash команды
    if tool_name != "Bash":
        return

    command = tool_input.get("command", "")

    # Проверяем каждый паттерн
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            # Блокируем! Exit code 2 = заблокировано
            print(json.dumps({
                "error": f"ЗАБЛОКИРОВАНО: команда '{command}' содержит опасный паттерн"
            }))
            sys.exit(2)


if __name__ == "__main__":
    main()
