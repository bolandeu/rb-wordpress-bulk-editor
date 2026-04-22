"""
Общий конфиг для хуков.
Пути определяются автоматически по платформе.
"""

import json
import os
import platform
import sys
from datetime import datetime

SANDBOX_PATH = os.path.expanduser("~/Projects/ai")
PROJECTS_ROOT = os.path.expanduser("~/Projects")

if platform.system() == "Darwin":
    VAULT_PATH = os.path.join(PROJECTS_ROOT, "Obsidian", "projects")
else:
    VAULT_PATH = os.path.expanduser("~/Documents/Obsidian/projects")

DAILY_PATH = os.path.join(VAULT_PATH, "_daily")


def get_project_name():
    """Определяем имя проекта по текущей директории."""
    path = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, "CLAUDE.md")):
            return os.path.basename(path)
        parent = os.path.dirname(path)
        if parent == path:
            return os.path.basename(os.getcwd())
        path = parent


def get_project_vault_path(project_name=None):
    """Путь к папке проекта в Obsidian."""
    if not project_name:
        project_name = get_project_name()
    return os.path.join(VAULT_PATH, project_name)


# --- Централизованные константы (single source of truth) ---

# Wiki vault структура
VAULT_DIRS = ["concepts", "connections", "queries"]

# Хуки: утилиты песочницы, НЕ копируются в проекты
SANDBOX_ONLY_HOOKS = {"verify-project.py", "project-resolver.py", "vault-search.py"}

# Docs: обязательные шаблоны проекта (копируются при rb-init, проверяются rb-verify)
REQUIRED_DOCS = ["PRD.md", "ARCHITECTURE.md", "TODO.md", "DECISIONS.md"]
# Docs: опциональные шаблоны
OPTIONAL_DOCS = ["API.md", "DATA-MODEL.md", "DEPLOY.md"]
# Docs: глобальные спеки — живут только в песочнице, НЕ копируются в проекты
GLOBAL_SPECS = {"WIKI.md", "SESSION-LOG-SYSTEM.md"}

# .gitignore: критичные паттерны (проверяются rb-verify, создаются rb-init)
GITIGNORE_CRITICAL = [
    ".env*",
    "!.env.example",
    "*.key",
    "*.pem",
    "*.p12",
    "*.db",
    "*.sqlite",
    "credentials*",
    "SESSION_LOG.md",
    ".DS_Store",
    ".claude/current-task.md",
]

HOOKS_ERROR_LOG = os.path.expanduser("~/.claude/hooks-errors.log")


def log_hook_error(hook_name, error):
    """Логирует ошибку хука в файл. Не бросает исключений."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {hook_name}: {error}\n"
        os.makedirs(os.path.dirname(HOOKS_ERROR_LOG), exist_ok=True)
        with open(HOOKS_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def safe_read_stdin(hook_name):
    """Читает и парсит JSON из stdin. При ошибке — логирует и возвращает None."""
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as e:
        log_hook_error(hook_name, f"invalid stdin JSON: {e}")
        return None
    except Exception as e:
        log_hook_error(hook_name, f"stdin read error: {e}")
        return None
