"""
Secret Scan Hook (Pre-Tool-Use)
Блокирует запись файлов содержащих секреты.

Сканирует content (Write) и new_string (Edit) на паттерны
API-ключей, токенов, паролей. Пустой stdout при success —
ноль контекста. Текст только при блокировке.
"""

import json
import os
import sys
import re

sys.path.insert(0, os.path.dirname(__file__))
from config import safe_read_stdin

# Паттерны секретов: (regex, описание)
SECRET_PATTERNS = [
    (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI API key'),
    (r'sk-ant-[a-zA-Z0-9\-]{20,}', 'Anthropic API key'),
    (r'ghp_[a-zA-Z0-9]{36,}', 'GitHub personal access token'),
    (r'gho_[a-zA-Z0-9]{36,}', 'GitHub OAuth token'),
    (r'ghu_[a-zA-Z0-9]{36,}', 'GitHub user token'),
    (r'ghs_[a-zA-Z0-9]{36,}', 'GitHub server token'),
    (r'github_pat_[a-zA-Z0-9_]{22,}', 'GitHub fine-grained PAT'),
    (r'glpat-[a-zA-Z0-9\-]{20,}', 'GitLab PAT'),
    (r'AKIA[0-9A-Z]{16}', 'AWS access key'),
    (r'xoxb-[0-9]{10,}-[a-zA-Z0-9\-]+', 'Slack bot token'),
    (r'xoxp-[0-9]{10,}-[a-zA-Z0-9\-]+', 'Slack user token'),
    (r'ya29\.[a-zA-Z0-9_\-]{20,}', 'Google OAuth token'),
    (r'AIza[a-zA-Z0-9_\-]{35}', 'Google API key'),
    (r'SG\.[a-zA-Z0-9_\-]{22}\.[a-zA-Z0-9_\-]{43}', 'SendGrid API key'),
    (r'sk_live_[a-zA-Z0-9]{24,}', 'Stripe secret key'),
    (r'rk_live_[a-zA-Z0-9]{24,}', 'Stripe restricted key'),
    (r'sq0atp-[a-zA-Z0-9\-_]{22,}', 'Square access token'),
    (r'eyJ[a-zA-Z0-9_\-]{30,}\.eyJ[a-zA-Z0-9_\-]{30,}', 'JWT token'),
    (r'npm_[a-zA-Z0-9]{36,}', 'npm access token'),
    (r'pypi-[a-zA-Z0-9]{50,}', 'PyPI API token'),
    (r'op://[a-zA-Z0-9/\-]+', '1Password secret ref (may be ok)'),
    (r'doppler://[a-zA-Z0-9/\-]+', 'Doppler secret ref (may be ok)'),
]

# Файлы где секреты ожидаемы — не сканируем
SKIP_PATTERNS = [
    r'\.example$',
    r'\.template$',
    r'\.sample$',
    r'secret-scan\.py$',  # этот файл
    r'CLAUDE\.md$',
    r'\.md$',  # документация может содержать примеры формата
]


def main():
    input_data = safe_read_stdin("secret-scan")
    if input_data is None:
        return
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return

    # Проверяем исключения по имени файла
    file_path = tool_input.get("file_path", "")
    for skip in SKIP_PATTERNS:
        if re.search(skip, file_path):
            return

    # Берём текст для проверки
    text = tool_input.get("content", "") or tool_input.get("new_string", "")
    if not text:
        return

    # Сканируем
    found = []
    for pattern, name in SECRET_PATTERNS:
        if re.search(pattern, text):
            found.append(name)

    if found:
        print(json.dumps({
            "error": f"ЗАБЛОКИРОВАНО: обнаружены секреты в {file_path}: {', '.join(found)}"
        }))
        sys.exit(2)


if __name__ == "__main__":
    main()
