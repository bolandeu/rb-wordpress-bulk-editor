"""
Команда describe: машиночитаемый контракт CLI (документ OpenCLI, `src/opencli.py`).
"""

import json

import typer

from . import app
from .. import opencli, output


@app.command("describe")
@output.guard
def describe(
    ctx: typer.Context,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Машиночитаемый контракт этого CLI: документ OpenCLI (opencli.org).

    Команды, опции, аргументы, коды выхода и метаданные x-rb-*: переменные
    окружения, файлы инструмента, имя дистрибутива. С --json — документ одной
    строкой (без конверта {"ok": …}); без него — тот же документ с отступами.

    Exit-коды: 0 — всегда.

    Example: {cli} describe --json
    """
    document = opencli.document(ctx.find_root().command)
    indent = None if output.is_json(ctx) else 2
    typer.echo(json.dumps(document, ensure_ascii=False, indent=indent))
