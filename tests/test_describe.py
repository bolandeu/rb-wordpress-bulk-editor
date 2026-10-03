"""The `describe --json` contract: a valid OpenCLI document that matches the real CLI.

Порт `tests/test_describe.py` шаблона cli-generator (схема OpenCLI — копия
`tests/data/opencli.schema.json` шаблона):

- документ валиден по схеме OpenCLI;
- в нём ровно зарегистрированные команды, опции — те, что видны в --help;
- коды выхода — таблица `EXIT_CODES` из `src/exceptions.py`, и каждый
  `exit_code = N` исключений в ней есть;
- переменные окружения — с `WP_API_USER` / `WP_API_TOKEN` (адаптер Windmill);
- snapshot: tests/snapshots/describe.json. Изменился контракт — тест падает,
  пока снапшот не обновлён `uv run pytest --snapshot-update` (изменение в diff).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import jsonschema
import pytest
import typer
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode
from typer.testing import CliRunner

from src import exceptions
from src.cli import app

CLI = "wp-bulk"
SCHEMA = json.loads((Path(__file__).parent / "data" / "opencli.schema.json").read_text())
HELP_ENV = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}
runner = CliRunner()


def _describe() -> dict:
    result = runner.invoke(app, ["describe", "--json"], prog_name=CLI)
    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 1, "--json prints the document on one line"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def document() -> dict:
    return _describe()


def _walk(command: dict, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], dict]]:
    for sub in command.get("commands", []):
        yield (*path, sub["name"]), sub
        yield from _walk(sub, (*path, sub["name"]))


def _registered(group: typer.Typer, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    for info in group.registered_commands:
        assert info.callback is not None
        yield (*prefix, info.name or getattr(info.callback, "__name__", "").replace("_", "-"))
    for sub in group.registered_groups:
        assert sub.name is not None and sub.typer_instance is not None
        yield (*prefix, sub.name)
        yield from _registered(sub.typer_instance, (*prefix, sub.name))


@pytest.fixture(scope="module")
def commands(document: dict) -> dict:
    return dict(_walk(document["command"]))


def _meta(entry: dict) -> dict:
    return {item["name"]: item.get("value") for item in entry.get("metadata", [])}


def test_valid_opencli_document(document: dict) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert document["info"]["title"] == CLI


def test_human_output_is_the_same_document(document: dict) -> None:
    result = runner.invoke(app, ["describe"], prog_name=CLI)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == document


def test_lists_exactly_the_registered_commands(commands: dict) -> None:
    assert set(commands) == set(_registered(app))


def test_adapter_commands_are_listed(commands: dict) -> None:
    for path in [("post", "upsert"), ("post", "get"), ("media", "attach"), ("describe",)]:
        assert path in commands, path


def test_options_are_the_real_ones(commands: dict) -> None:
    for path, command in sorted(commands.items()):
        result = runner.invoke(app, [*path, "--help"], env=HELP_ENV, prog_name=CLI)
        assert result.exit_code == 0, result.output
        for option in command.get("options", []):
            if not option.get("hidden"):
                assert option["name"] in result.output, f"{' '.join(path)}: {option['name']} isn't in --help"


def test_every_exit_code_is_documented(document: dict) -> None:
    """Каждый `exit_code = N` исключений из src/exceptions.py есть в EXIT_CODES, а значит в документе."""
    exit_codes = getattr(exceptions, "EXIT_CODES", {})
    used = {int(code) for code in re.findall(r"exit_code = (\d+)", Path(exceptions.__file__).read_text())}
    assert used <= set(exit_codes)
    assert [entry["code"] for entry in document["command"]["exitCodes"]] == list(exit_codes)


def test_root_metadata(document: dict) -> None:
    meta = _meta(document["command"])
    assert meta["x-rb-tool"]["dist"] == "rb-wordpress-bulk-editor"
    assert "user_log_dir" in meta["x-rb-data"]
    names = [entry["name"] for entry in meta["x-rb-env"]]
    assert len(names) == len(set(names))
    assert {"WP_API_USER", "WP_API_TOKEN"} <= set(names)


def test_password_options_are_hidden(commands: dict) -> None:
    """--password у адаптерных команд — только чтобы отказать с понятным сообщением."""
    for path in [("post", "upsert"), ("post", "get"), ("media", "attach")]:
        options = {o["name"]: o for o in commands[path].get("options", [])}
        assert options["--password"]["hidden"] is True


class DescribeSnapshot(SingleFileSnapshotExtension):
    """tests/snapshots/describe.json, pretty-printed for readable diffs."""

    _write_mode = WriteMode.TEXT
    file_extension = "json"

    @classmethod
    def dirname(cls, *, test_location) -> str:
        return str(Path(__file__).parent / "snapshots")

    @classmethod
    def get_snapshot_name(cls, *, test_location, index=0) -> str:
        return "describe"


def test_describe_snapshot(document: dict, snapshot) -> None:
    document = json.loads(json.dumps(document))
    # Версия меняется с каждым релизом — это не изменение контракта.
    document["info"]["version"] = "<version>"
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    assert rendered == snapshot(extension_class=DescribeSnapshot)
