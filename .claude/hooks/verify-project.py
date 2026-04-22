"""
Project Verification — проверяет что проект правильно инициализирован
и хуки/docs актуальны относительно песочницы.

Использование:
    python verify-project.py [project-path]

Без аргумента — ищет проект от cwd вверх по CLAUDE.md.
Вывод: JSON с результатами проверок.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    SANDBOX_PATH, VAULT_PATH, VAULT_DIRS, SANDBOX_ONLY_HOOKS,
    REQUIRED_DOCS, OPTIONAL_DOCS, GLOBAL_SPECS, GITIGNORE_CRITICAL,
)


def file_hash(path):
    """MD5 hash файла для сравнения."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except (IOError, OSError):
        return None


def find_project_root(start=None):
    path = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, "CLAUDE.md")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def check_structure(root):
    """Проверяет наличие обязательных файлов и папок."""
    issues = []
    required_files = [
        ("CLAUDE.md", "Главный файл проекта"),
        (".claude/settings.local.json", "Настройки хуков и permissions"),
    ]
    required_dirs = [
        (".claude/hooks", "Папка хуков"),
        ("docs", "Документация"),
    ]

    for path, desc in required_files:
        full = os.path.join(root, path)
        if not os.path.exists(full):
            issues.append({"severity": "critical", "check": "structure", "msg": f"Отсутствует {path} — {desc}"})
        elif os.path.getsize(full) == 0:
            issues.append({"severity": "warning", "check": "structure", "msg": f"{path} пуст — {desc}"})

    for path, desc in required_dirs:
        full = os.path.join(root, path)
        if not os.path.isdir(full):
            issues.append({"severity": "critical", "check": "structure", "msg": f"Отсутствует {path}/ — {desc}"})

    return issues


def check_hooks(root):
    """Сверяет хуки проекта с эталоном в песочнице."""
    issues = []
    sandbox_hooks = os.path.join(SANDBOX_PATH, ".claude", "hooks")
    project_hooks = os.path.join(root, ".claude", "hooks")

    if not os.path.isdir(project_hooks):
        issues.append({"severity": "critical", "check": "hooks", "msg": "Папка .claude/hooks/ отсутствует"})
        return issues

    # Все .py файлы из песочницы (кроме тестов и служебных)
    sandbox_files = {
        f for f in os.listdir(sandbox_hooks)
        if f.endswith(".py") and f not in SANDBOX_ONLY_HOOKS and not f.startswith("test_")
    }

    project_files = {
        f for f in os.listdir(project_hooks)
        if f.endswith(".py")
    }

    # Отсутствующие хуки
    missing = sandbox_files - project_files
    for f in sorted(missing):
        issues.append({"severity": "critical", "check": "hooks", "msg": f"Хук {f} отсутствует в проекте"})

    # Устаревшие хуки (хеш не совпадает)
    common = sandbox_files & project_files
    for f in sorted(common):
        sandbox_hash = file_hash(os.path.join(sandbox_hooks, f))
        project_hash = file_hash(os.path.join(project_hooks, f))
        if sandbox_hash and project_hash and sandbox_hash != project_hash:
            issues.append({
                "severity": "warning",
                "check": "hooks",
                "msg": f"Хук {f} устарел (отличается от песочницы)"
            })

    # Лишние хуки (есть в проекте, нет в песочнице)
    extra = project_files - sandbox_files - {"__pycache__"}
    for f in sorted(extra):
        if not f.startswith("__"):
            issues.append({"severity": "info", "check": "hooks", "msg": f"Хук {f} — кастомный (нет в песочнице)"})

    return issues


def check_docs(root):
    """Проверяет наличие и наполненность docs/."""
    issues = []
    docs_dir = os.path.join(root, "docs")

    if not os.path.isdir(docs_dir):
        return [{"severity": "critical", "check": "docs", "msg": "Папка docs/ отсутствует"}]

    required = REQUIRED_DOCS
    optional = OPTIONAL_DOCS

    for f in required:
        full = os.path.join(docs_dir, f)
        if not os.path.exists(full):
            issues.append({"severity": "warning", "check": "docs", "msg": f"docs/{f} отсутствует"})
        else:
            size = os.path.getsize(full)
            if size < 50:
                issues.append({"severity": "warning", "check": "docs", "msg": f"docs/{f} почти пуст ({size} байт) — шаблон не заполнен?"})

    for f in optional:
        full = os.path.join(docs_dir, f)
        if os.path.exists(full):
            size = os.path.getsize(full)
            if size < 50:
                issues.append({"severity": "info", "check": "docs", "msg": f"docs/{f} почти пуст ({size} байт)"})

    return issues


def check_settings(root):
    """Проверяет settings.local.json на корректность."""
    issues = []
    settings_path = os.path.join(root, ".claude", "settings.local.json")

    if not os.path.exists(settings_path):
        return [{"severity": "critical", "check": "settings", "msg": ".claude/settings.local.json отсутствует"}]

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except json.JSONDecodeError as e:
        return [{"severity": "critical", "check": "settings", "msg": f"settings.local.json — невалидный JSON: {e}"}]

    # Проверяем наличие ключевых секций
    if "hooks" not in settings:
        issues.append({"severity": "critical", "check": "settings", "msg": "settings.local.json: нет секции 'hooks'"})
    else:
        hooks = settings["hooks"]
        expected_events = ["PreToolUse", "PostToolUse", "Stop"]
        for event in expected_events:
            if event not in hooks:
                issues.append({"severity": "warning", "check": "settings", "msg": f"settings: нет хуков для события {event}"})

    if "permissions" not in settings:
        issues.append({"severity": "warning", "check": "settings", "msg": "settings.local.json: нет секции 'permissions'"})
    else:
        perms = settings["permissions"]
        if "deny" not in perms or not perms["deny"]:
            issues.append({"severity": "warning", "check": "settings", "msg": "settings: deny-список пуст — нет защиты от опасных команд"})

    return issues


def check_vault(root):
    """Проверяет наличие wiki в Obsidian vault."""
    issues = []
    project_name = os.path.basename(root)
    vault_dir = os.path.join(VAULT_PATH, project_name)

    if not os.path.isdir(vault_dir):
        issues.append({"severity": "warning", "check": "vault", "msg": f"Wiki vault отсутствует: {vault_dir}"})
        return issues

    expected = ["log.md", "index.md"]
    for f in expected:
        if not os.path.exists(os.path.join(vault_dir, f)):
            issues.append({"severity": "warning", "check": "vault", "msg": f"Vault: {f} отсутствует"})

    for d in VAULT_DIRS:
        if not os.path.isdir(os.path.join(vault_dir, d)):
            issues.append({"severity": "info", "check": "vault", "msg": f"Vault: папка {d}/ отсутствует"})

    return issues


def check_gitignore(root):
    """Проверяет что .gitignore покрывает критичные паттерны."""
    issues = []
    gi_path = os.path.join(root, ".gitignore")

    if not os.path.exists(gi_path):
        return [{"severity": "warning", "check": "gitignore", "msg": ".gitignore отсутствует"}]

    with open(gi_path) as f:
        content = f.read()

    # Проверяем только паттерны без отрицания (!)
    check_patterns = [p for p in GITIGNORE_CRITICAL if not p.startswith("!")]
    for pattern in check_patterns:
        # Простая проверка — ищем паттерн в тексте
        if pattern not in content and pattern.replace("*", "") not in content:
            issues.append({"severity": "warning", "check": "gitignore", "msg": f".gitignore не содержит '{pattern}'"})

    return issues


def fix_issues(root, issues):
    """Автоматически исправляет то, что можно исправить без потери данных."""
    fixed = []
    project_name = os.path.basename(root)
    sandbox_hooks = os.path.join(SANDBOX_PATH, ".claude", "hooks")
    project_hooks = os.path.join(root, ".claude", "hooks")

    for issue in issues:
        check = issue["check"]
        sev = issue["severity"]
        msg = issue["msg"]

        # --- Hooks: устаревшие или отсутствующие ---
        if check == "hooks" and "устарел" in msg:
            hook_name = msg.split()[1]
            src = os.path.join(sandbox_hooks, hook_name)
            dst = os.path.join(project_hooks, hook_name)
            if os.path.exists(src):
                # Backup
                if os.path.exists(dst):
                    backup = dst + ".bak"
                    import shutil
                    shutil.copy2(dst, backup)
                import shutil
                shutil.copy2(src, dst)
                fixed.append(f"Обновлён: {hook_name}")

        elif check == "hooks" and "отсутствует в проекте" in msg:
            hook_name = msg.split()[1]
            src = os.path.join(sandbox_hooks, hook_name)
            dst = os.path.join(project_hooks, hook_name)
            if os.path.exists(src):
                import shutil
                shutil.copy2(src, dst)
                fixed.append(f"Добавлен: {hook_name}")

        elif check == "hooks" and "Папка .claude/hooks/ отсутствует" in msg:
            os.makedirs(project_hooks, exist_ok=True)
            # Копируем все хуки
            import shutil
            for f in os.listdir(sandbox_hooks):
                if f.endswith(".py") and f not in SANDBOX_ONLY_HOOKS and not f.startswith("test_"):
                    shutil.copy2(os.path.join(sandbox_hooks, f), os.path.join(project_hooks, f))
            fixed.append("Создана .claude/hooks/ и скопированы все хуки")

        # --- Structure: settings ---
        elif check == "structure" and "settings.local.json" in msg:
            src = os.path.join(SANDBOX_PATH, "settings.example.json")
            dst = os.path.join(root, ".claude", "settings.local.json")
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                import shutil
                shutil.copy2(src, dst)
                fixed.append("Создан .claude/settings.local.json из шаблона")

        # --- Settings: нет hooks/permissions ---
        elif check == "settings" and sev == "critical" and "нет секции 'hooks'" in msg:
            settings_path = os.path.join(root, ".claude", "settings.local.json")
            src = os.path.join(SANDBOX_PATH, "settings.example.json")
            if os.path.exists(src) and os.path.exists(settings_path):
                # Backup и заменяем
                import shutil
                shutil.copy2(settings_path, settings_path + ".bak")
                shutil.copy2(src, settings_path)
                fixed.append("settings.local.json заменён эталоном (backup: .bak)")

        # --- Vault: создание структуры ---
        elif check == "vault" and "Wiki vault отсутствует" in msg:
            vault_dir = os.path.join(VAULT_PATH, project_name)
            os.makedirs(vault_dir, exist_ok=True)
            for d in VAULT_DIRS:
                os.makedirs(os.path.join(vault_dir, d), exist_ok=True)
            # log.md
            log_path = os.path.join(vault_dir, "log.md")
            if not os.path.exists(log_path):
                with open(log_path, "w") as f:
                    f.write(f"# {project_name} — лог\n")
            # index.md
            idx_path = os.path.join(vault_dir, "index.md")
            if not os.path.exists(idx_path):
                with open(idx_path, "w") as f:
                    f.write(f"# {project_name} — Wiki Index\n\n| Статья | Summary | Источник | Обновлено |\n|--------|---------|----------|-----------|\n")
            fixed.append(f"Создана wiki-структура: {vault_dir}")

        elif check == "vault" and "отсутствует" in msg and ("log.md" in msg or "index.md" in msg):
            vault_dir = os.path.join(VAULT_PATH, project_name)
            fname = "log.md" if "log.md" in msg else "index.md"
            fpath = os.path.join(vault_dir, fname)
            if not os.path.exists(fpath) and os.path.isdir(vault_dir):
                with open(fpath, "w") as f:
                    if fname == "log.md":
                        f.write(f"# {project_name} — лог\n")
                    else:
                        f.write(f"# {project_name} — Wiki Index\n\n| Статья | Summary | Источник | Обновлено |\n|--------|---------|----------|-----------|\n")
                fixed.append(f"Создан vault/{fname}")

        elif check == "vault" and "папка" in msg and "отсутствует" in msg:
            vault_dir = os.path.join(VAULT_PATH, project_name)
            for d in VAULT_DIRS:
                if d in msg:
                    dpath = os.path.join(vault_dir, d)
                    if not os.path.exists(dpath) and os.path.isdir(vault_dir):
                        os.makedirs(dpath, exist_ok=True)
                        fixed.append(f"Создана vault/{d}/")

        # --- Docs: копируем недостающие обязательные шаблоны из песочницы ---
        elif check == "docs" and "Папка docs/ отсутствует" in msg:
            os.makedirs(os.path.join(root, "docs"), exist_ok=True)
            sandbox_docs = os.path.join(SANDBOX_PATH, "docs")
            import shutil
            # Только проектные шаблоны, НЕ глобальные спеки
            for f in os.listdir(sandbox_docs):
                if f.endswith(".md") and f not in GLOBAL_SPECS:
                    shutil.copy2(os.path.join(sandbox_docs, f), os.path.join(root, "docs", f))
            fixed.append("Создана docs/ и скопированы шаблоны")

        elif check == "docs" and "отсутствует" in msg and "docs/" in msg:
            fname = msg.split("docs/")[1].split(" ")[0]
            src = os.path.join(SANDBOX_PATH, "docs", fname)
            dst = os.path.join(root, "docs", fname)
            if os.path.exists(src) and not os.path.exists(dst):
                os.makedirs(os.path.join(root, "docs"), exist_ok=True)
                import shutil
                shutil.copy2(src, dst)
                fixed.append(f"Скопирован шаблон: docs/{fname}")

        # --- Gitignore ---
        elif check == "gitignore" and "не содержит" in msg:
            gi_path = os.path.join(root, ".gitignore")
            # Извлекаем паттерн
            pattern = msg.split("'")[1]
            if os.path.exists(gi_path):
                with open(gi_path, "a") as f:
                    f.write(f"\n{pattern}\n")
                fixed.append(f"Добавлен в .gitignore: {pattern}")

    return fixed


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Verify project setup")
    parser.add_argument("path", nargs="?", help="Project path")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues")
    args = parser.parse_args()

    # Определяем проект
    if args.path:
        root = os.path.abspath(args.path)
        if not os.path.exists(os.path.join(root, "CLAUDE.md")):
            print(json.dumps({"error": f"Не проект (нет CLAUDE.md): {root}"}))
            sys.exit(1)
    else:
        root = find_project_root()
        if not root:
            print(json.dumps({"error": "Проект не найден. Запусти из папки проекта или укажи путь."}))
            sys.exit(1)

    project_name = os.path.basename(root)

    # Запускаем все проверки
    all_issues = []
    all_issues.extend(check_structure(root))
    all_issues.extend(check_hooks(root))
    all_issues.extend(check_docs(root))
    all_issues.extend(check_settings(root))
    all_issues.extend(check_vault(root))
    all_issues.extend(check_gitignore(root))

    # Авто-починка
    fixed = []
    if args.fix and all_issues:
        fixed = fix_issues(root, all_issues)
        # Перепроверяем после починки
        all_issues = []
        all_issues.extend(check_structure(root))
        all_issues.extend(check_hooks(root))
        all_issues.extend(check_docs(root))
        all_issues.extend(check_settings(root))
        all_issues.extend(check_vault(root))
        all_issues.extend(check_gitignore(root))

    # Подсчёт
    counts = {"critical": 0, "warning": 0, "info": 0}
    for issue in all_issues:
        counts[issue["severity"]] += 1

    result = {
        "project": project_name,
        "path": root,
        "issues": all_issues,
        "counts": counts,
        "total": len(all_issues),
        "healthy": counts["critical"] == 0,
    }
    if fixed:
        result["fixed"] = fixed

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if counts["critical"] == 0 else 1)


if __name__ == "__main__":
    main()
