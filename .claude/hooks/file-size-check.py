"""
File Size Check Hook (Post-Tool-Use)
Предупреждает когда файл превышает 300 строк — предложи разбить.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from config import safe_read_stdin

SKIP = [".claude/", "node_modules/", ".git/", "package-lock", "uv.lock", ".min.", ".map"]
MAX_LINES = 300


def main():
    input_data = safe_read_stdin("file-size-check")
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

    if not os.path.exists(file_path):
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = sum(1 for _ in f)
    except (UnicodeDecodeError, IOError):
        return

    if lines > MAX_LINES:
        print(f"⚠️ {os.path.basename(file_path)}: {lines} строк (>{MAX_LINES}). Рассмотри разбиение на модули.")


if __name__ == "__main__":
    main()
