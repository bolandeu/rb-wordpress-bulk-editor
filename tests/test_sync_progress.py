"""
Tests for счётчика прогресса синхронизации: строка `[17/104] id=3425 updated`
по завершении каждой записи — по ней вызывающий (antagor) видит долю сделанного.
"""

import pytest
from loguru import logger

from src.records.base import Record
from src.sync.progress import Progress


@pytest.fixture
def lines():
    captured = []
    sink_id = logger.add(lambda m: captured.append(m.record["message"]), level="INFO", format="{message}")
    yield captured
    logger.remove(sink_id)


def rec(key, **data):
    return Record(discriminator="posts", is_taxonomy=False, data=data, write_key=key)


class TestProgress:
    def test_success_uses_id_from_response(self, lines):
        progress = Progress(total=104)
        progress.skipped(16)

        progress.record_done(rec(2, id=""), "add", {"id": 3425})

        assert lines[-1] == "[17/104] id=3425 added"

    def test_update_and_delete(self, lines):
        progress = Progress(total=2)

        progress.record_done(rec(2, id=7), "update", {"id": 7})
        progress.record_done(rec(3, id=8), "delete", {"deleted": True})

        assert lines == ["[1/2] id=7 updated", "[2/2] id=8 deleted"]

    def test_error_without_id_points_to_record(self, lines):
        progress = Progress(total=1)

        progress.record_done(rec(5, id=""), "add", error=True)

        assert lines == ["[1/1] id=- error (запись 5)"]

    def test_dry_run_placeholder_id_falls_back_to_record_id(self, lines):
        progress = Progress(total=1)

        progress.record_done(rec(2, id=9), "update", {"id": 0, "dry_run": True})

        assert lines == ["[1/1] id=9 updated"]

    def test_skipped_are_counted_in_one_line(self, lines):
        progress = Progress(total=10)

        progress.skipped(0)
        progress.skipped(3)

        assert lines == ["[3/10] пропущено записей (skip/пустой action): 3"]
