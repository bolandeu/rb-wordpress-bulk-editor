"""
Человеческий вывод: rich-консоль в том же стиле, что и команды сегодня.

stdout — только данные (успешный результат), stderr — ошибки, прогресс и
диагностика. Тогда `wp-bulk options > out.txt` не поймает «ERROR: ...» как данные.
"""

from typing import Any, Mapping

from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def echo(message: str) -> None:
    """Печать строки в stdout."""
    console.print(message)


def success(message: str) -> None:
    """Итоговое сообщение об успехе."""
    console.print(f"\n[bold green]{message}[/bold green]\n")


def key_values(title: str, pairs: Mapping[str, Any]) -> None:
    """Блок «заголовок + пары ключ: значение»."""
    console.print(f"[bold]{title}[/bold]")
    for key, value in pairs.items():
        console.print(f"  {key}: [cyan]{value}[/cyan]")


def error(message: str, action: str = "") -> None:
    """Ошибка в stderr: сообщение и (если есть) что с этим делать."""
    err_console.print(f"\n[bold red]ERROR: {message}[/bold red]")
    if action:
        err_console.print(f"[dim]{action}[/dim]")
    err_console.print("")
