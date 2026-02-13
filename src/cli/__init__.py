"""
CLI интерфейс для WordPress Bulk Editor

Модульная структура:
- sync_commands.py    - sync, async (синхронизация Excel -> WordPress)
- fetch_commands.py   - fetch, fetch-media (загрузка WordPress -> Excel)
- export_commands.py  - export, export-media (экспорт контента/медиа в файлы)
- google_commands.py  - download-google-sheet, upload-to-google-sheets,
                        sync-from-google, sync-google-bidirectional,
                        async-from-google, async-google-bidirectional
- tool_commands.py    - validate, options, bulk-delete, counter, llm
"""

import typer
from rich.console import Console

app = typer.Typer(
    name="wp-bulk-editor",
    help="WordPress Bulk Editor - Excel <-> WordPress sync"
)
console = Console()

# Регистрируем команды из подмодулей
# Импорты размещены после создания app, чтобы подмодули могли импортировать app
from . import sync_commands  # noqa: E402, F401
from . import fetch_commands  # noqa: E402, F401
from . import export_commands  # noqa: E402, F401
from . import google_commands  # noqa: E402, F401
from . import tool_commands  # noqa: E402, F401


def main():
    """Точка входа"""
    app()
