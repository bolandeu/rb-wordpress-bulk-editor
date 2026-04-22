"""
Commit Lint Hook (Pre-Tool-Use)
Проверяет формат conventional commit перед git commit.
"""

import json
import os
import sys
import re

sys.path.insert(0, os.path.dirname(__file__))
from config import safe_read_stdin

COMMIT_PATTERN = re.compile(
    r'^(feat|fix|refactor|test|docs|chore|build|ci|style|perf|revert)'
    r'(\([a-zA-Z0-9_\-]+\))?'
    r': .{3,}'
)


def main():
    input_data = safe_read_stdin("commit-lint")
    if input_data is None:
        return
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name != "Bash":
        return

    command = tool_input.get("command", "")

    if "git commit" not in command:
        return

    # Извлекаем сообщение коммита
    # git commit -m "message" или git commit -m "$(cat <<'EOF' ... EOF)"
    match = re.search(r'-m\s+["\']([^"\']+)["\']', command)
    if not match:
        # heredoc или другой формат — пропускаем, не блокируем
        return

    message = match.group(1).strip()

    # Пропускаем shell substitution / heredoc
    if message.startswith("$(") or "<<" in message:
        return

    # Первая строка
    first_line = message.split("\n")[0]

    # Пропускаем merge commits и Co-Authored-By
    if first_line.startswith("Merge") or first_line.startswith("Co-Authored"):
        return

    if not COMMIT_PATTERN.match(first_line):
        print(json.dumps({
            "error": (
                f"Коммит не соответствует conventional commits: '{first_line}'. "
                f"Формат: type(scope): описание. "
                f"Типы: feat, fix, refactor, test, docs, chore, build, ci, style, perf, revert"
            )
        }))
        sys.exit(2)


if __name__ == "__main__":
    main()
