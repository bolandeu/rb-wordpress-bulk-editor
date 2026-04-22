"""
Vault Search — полнотекстовый поиск по базе знаний.
Ищет по vault (Obsidian), docs/, DECISIONS.md, CHANGELOG.md, CLAUDE.md.

Использование:
    python vault-search.py "запрос" [--scope=all|vault|docs|decisions|git] [--project=имя] [--limit=10] [--context=2]

Примеры:
    python vault-search.py "миграция auth"
    python vault-search.py "content_hash" --scope=docs --project=doit
    python vault-search.py "выбор между" --scope=decisions --limit=5
"""

import argparse
import json
import os
import re
import sys
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import VAULT_PATH, DAILY_PATH

PROJECTS_ROOT = os.path.expanduser("~/Projects")
INDEX_PATH = os.path.join(VAULT_PATH, "_index.json")


def find_projects():
    """Найти все проекты с CLAUDE.md (до 4 уровней)."""
    projects = {}
    for depth in range(1, 5):
        pattern = os.path.join(PROJECTS_ROOT, *["*"] * depth, "CLAUDE.md")
        for claude_md in glob.glob(pattern):
            project_dir = os.path.dirname(claude_md)
            name = os.path.basename(project_dir)
            if name not in projects:
                projects[name] = project_dir
    return projects


def collect_files(scope, project_name=None):
    """Собрать файлы для поиска по scope."""
    files = []
    projects = find_projects()

    if project_name:
        if project_name not in projects:
            print(f"Проект '{project_name}' не найден", file=sys.stderr)
            sys.exit(1)
        target_projects = {project_name: projects[project_name]}
    else:
        target_projects = projects

    for name, path in target_projects.items():
        if scope in ("all", "docs"):
            docs_dir = os.path.join(path, "docs")
            if os.path.isdir(docs_dir):
                for f in glob.glob(os.path.join(docs_dir, "**", "*.md"), recursive=True):
                    files.append(("docs", name, f))

        if scope in ("all", "decisions"):
            dec = os.path.join(path, "docs", "DECISIONS.md")
            if os.path.exists(dec):
                files.append(("decisions", name, dec))

        if scope in ("all", "docs"):
            claude = os.path.join(path, "CLAUDE.md")
            if os.path.exists(claude):
                files.append(("claude.md", name, claude))

            changelog = os.path.join(path, "CHANGELOG.md")
            if os.path.exists(changelog):
                files.append(("changelog", name, changelog))

        if scope in ("all", "git"):
            # Собираем git log в temp-файл для поиска
            import subprocess
            import tempfile
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "-100", "--no-merges"],
                    cwd=path, capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    tmp = os.path.join(tempfile.gettempdir(), f"vault-search-git-{name}.md")
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(f"# git log — {name}\n\n")
                        f.write(result.stdout)
                    files.append(("git", name, tmp))
            except (subprocess.TimeoutExpired, OSError):
                pass

    if scope in ("all", "vault"):
        vault_dirs = glob.glob(os.path.join(VAULT_PATH, "*"))
        for vdir in vault_dirs:
            if not os.path.isdir(vdir) or os.path.basename(vdir).startswith("_"):
                continue
            vname = os.path.basename(vdir)
            if project_name and vname != project_name:
                continue
            for f in glob.glob(os.path.join(vdir, "**", "*.md"), recursive=True):
                files.append(("vault", vname, f))

        # Daily notes
        if not project_name:
            for f in sorted(glob.glob(os.path.join(DAILY_PATH, "*.md")))[-30:]:
                files.append(("daily", "_daily", f))

    return files


def search(query, files, context_lines=2, limit=10):
    """Поиск по файлам. Возвращает результаты с контекстом."""
    # Разбиваем запрос на слова для ранжирования
    terms = [t.lower() for t in query.split() if len(t) > 1]
    results = []

    for source_type, project, filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue

        for i, line in enumerate(lines):
            line_lower = line.lower()
            # Считаем сколько термов найдено в строке
            score = sum(1 for t in terms if t in line_lower)
            if score == 0:
                continue

            # Контекст
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            ctx = "".join(lines[start:end]).strip()

            rel_path = os.path.relpath(filepath, PROJECTS_ROOT)
            results.append({
                "score": score,
                "source": source_type,
                "project": project,
                "file": rel_path,
                "line": i + 1,
                "context": ctx,
            })

    # Сортируем по score (desc), потом по файлу
    results.sort(key=lambda r: (-r["score"], r["file"], r["line"]))

    # Дедупликация — убираем перекрывающиеся контексты из одного файла
    seen = set()
    deduped = []
    for r in results:
        key = (r["file"], r["line"] // (context_lines * 2 + 1))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped[:limit]


def update_index(files):
    """Обновить/создать _index.json если файлы изменились."""
    index = {}
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r") as f:
                index = json.load(f)
        except (json.JSONDecodeError, OSError):
            index = {}

    updated = False
    new_index = {}

    for source_type, project, filepath in files:
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue

        rel_path = os.path.relpath(filepath, PROJECTS_ROOT)
        cached = index.get(rel_path)

        if cached and cached.get("mtime") == mtime:
            new_index[rel_path] = cached
            continue

        # Нужно обновить
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(500)
        except OSError:
            continue

        # Извлекаем title из первого заголовка
        title_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else os.path.basename(filepath)

        new_index[rel_path] = {
            "title": title,
            "source": source_type,
            "project": project,
            "summary": content[:200].replace("\n", " ").strip(),
            "mtime": mtime,
        }
        updated = True

    if updated:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(new_index, f, ensure_ascii=False, indent=2)

    return new_index


def format_results(results, query):
    """Форматировать результаты для вывода."""
    if not results:
        print(f"Ничего не найдено по запросу: {query}")
        return

    print(f"## Результаты поиска: \"{query}\" ({len(results)} совпадений)\n")
    for i, r in enumerate(results, 1):
        print(f"### {i}. [{r['source']}] {r['project']} — {r['file']}:{r['line']}")
        print(f"```")
        print(r["context"])
        print(f"```")
        print()


def main():
    parser = argparse.ArgumentParser(description="Поиск по базе знаний")
    parser.add_argument("query", help="Поисковый запрос")
    parser.add_argument("--scope", default="all",
                       choices=["all", "vault", "docs", "decisions", "git"],
                       help="Область поиска")
    parser.add_argument("--project", default=None, help="Имя проекта")
    parser.add_argument("--limit", type=int, default=10, help="Макс. результатов")
    parser.add_argument("--context", type=int, default=2, help="Строк контекста")
    parser.add_argument("--index", action="store_true", help="Обновить индекс и показать статистику")
    parser.add_argument("--json", action="store_true", help="Вывод в JSON")

    args = parser.parse_args()
    files = collect_files(args.scope, args.project)

    # Обновляем индекс при каждом вызове
    idx = update_index(files)

    if args.index:
        print(f"Индекс: {len(idx)} файлов")
        by_source = {}
        for entry in idx.values():
            s = entry["source"]
            by_source[s] = by_source.get(s, 0) + 1
        for s, c in sorted(by_source.items()):
            print(f"  {s}: {c}")
        return

    results = search(args.query, files, args.context, args.limit)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        format_results(results, args.query)


if __name__ == "__main__":
    main()
