"""
Tests for гейта необратимых удалений в sync/async.

Обратимую часть sync (контент в корзину) гейт не трогает; спрашивает только
там, где удаление постоянное: media-строки с action=delete и --delete-media.
"""

from types import SimpleNamespace

import click
import pandas as pd
import pytest
import typer

from src import output
from src.cli.sync_commands import _confirm_irreversible, _count_permanent_media_deletes
from src.config import SyncConfig
from src.exceptions import ValidationError


class FakeReader:
    """Минимальный ExcelReader: классификация листа + сам лист."""

    def __init__(self, sheets: dict):
        self.sheets = sheets

    def classify_sheet(self, name: str) -> str:
        return "media" if name == "media" else "content"

    def read_sheet(self, name: str) -> pd.DataFrame:
        return self.sheets[name]


def make_reader(media_actions=(), content_actions=()) -> FakeReader:
    return FakeReader(
        {
            "media": pd.DataFrame({"action": list(media_actions), "id": range(len(media_actions))}),
            "posts": pd.DataFrame({"action": list(content_actions), "id": range(len(content_actions))}),
        }
    )


def make_config(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(sync=SyncConfig(**kwargs))


def make_ctx(non_interactive: bool = True) -> typer.Context:
    ctx = typer.Context(click.Command("sync"))
    output.get_options(ctx).non_interactive = non_interactive
    return ctx


SHEETS = ["posts", "media"]


class TestCountPermanentMediaDeletes:
    def test_counts_only_media_delete_rows(self):
        reader = make_reader(media_actions=["delete", "add", "DELETE"], content_actions=["delete"])
        assert _count_permanent_media_deletes(reader, SHEETS, make_config()) == 2

    def test_skip_media_means_nothing_is_deleted(self):
        reader = make_reader(media_actions=["delete", "delete"])
        assert _count_permanent_media_deletes(reader, SHEETS, make_config(skip_media=True)) == 0

    def test_media_sheet_not_in_run_is_not_counted(self):
        reader = make_reader(media_actions=["delete"])
        assert _count_permanent_media_deletes(reader, ["posts"], make_config()) == 0


class TestConfirmIrreversible:
    def test_content_only_run_is_not_gated(self):
        reader = make_reader(media_actions=["add"], content_actions=["delete", "update"])
        _confirm_irreversible(make_ctx(), reader, SHEETS, make_config(), confirm=False)

    def test_dry_run_never_asks(self):
        reader = make_reader(media_actions=["delete"])
        _confirm_irreversible(make_ctx(), reader, SHEETS, make_config(dry_run=True), confirm=False)

    def test_media_delete_requires_confirm(self):
        reader = make_reader(media_actions=["delete"])

        with pytest.raises(ValidationError) as exc:
            _confirm_irreversible(make_ctx(), reader, SHEETS, make_config(), confirm=False)

        assert exc.value.exit_code == 2
        assert "навсегда" in exc.value.message

    def test_delete_featured_media_requires_confirm(self):
        reader = make_reader(content_actions=["delete"])

        with pytest.raises(ValidationError) as exc:
            _confirm_irreversible(make_ctx(), reader, SHEETS, make_config(delete_featured_media=True), confirm=False)

        assert "--delete-media" in exc.value.message

    def test_confirm_flag_lets_it_through(self):
        reader = make_reader(media_actions=["delete"])
        _confirm_irreversible(make_ctx(), reader, SHEETS, make_config(delete_featured_media=True), confirm=True)
