"""
Tests for src/sync/orchestrator.py — группировка записей по дискриминатору
"""

from rb_wordpress_bulk_editor.models import SyncReport
from rb_wordpress_bulk_editor.records.base import Record
from rb_wordpress_bulk_editor.sync.orchestrator import merge_reports, sync_record_groups


class RecordingSync:
    """Фейковый sync: запоминает вызовы и отдаёт отчёт с total=len(records)."""

    def __init__(self):
        self.calls = []

    async def sync_records(self, discriminator, records, writer=None, progress=None):
        self.calls.append((discriminator, list(records)))
        return SyncReport.from_stats({"total": len(records), "added": len(records)})


def _record(discriminator: str, is_taxonomy: bool, key: int) -> Record:
    return Record(discriminator, is_taxonomy, {"action": "add"}, key)


class TestSyncRecordGroups:
    async def test_groups_mixed_records_by_discriminator(self):
        content_sync = RecordingSync()
        taxonomy_sync = RecordingSync()

        records = [
            _record("rb_services", False, 2),
            _record("rb_services_cat", True, 3),
            _record("page", False, 4),
            _record("rb_services", False, 5),
        ]

        report = await sync_record_groups(records, content_sync, taxonomy_sync)

        assert [d for d, _ in content_sync.calls] == ["rb_services", "page"]
        assert [len(r) for _, r in content_sync.calls] == [2, 1]
        assert [d for d, _ in taxonomy_sync.calls] == ["rb_services_cat"]
        assert report.total == 4
        assert report.created == 4

    async def test_homogeneous_records_produce_single_call(self):
        """Лист Excel однороден — группировка не должна его дробить."""
        content_sync = RecordingSync()
        taxonomy_sync = RecordingSync()

        records = [_record("posts", False, i) for i in range(3)]
        await sync_record_groups(records, content_sync, taxonomy_sync)

        assert len(content_sync.calls) == 1
        assert taxonomy_sync.calls == []


class TestMergeReports:
    def test_sums_stats_and_errors(self):
        first = SyncReport.from_stats({"total": 2, "added": 1, "errors": 1})
        second = SyncReport.from_stats({"total": 3, "updated": 2, "skipped": 1})

        merged = merge_reports([first, second])

        assert merged.total == 5
        assert merged.created == 1
        assert merged.updated == 2
        assert merged.skipped == 1
        assert merged.failed == 1

    def test_empty_list(self):
        assert merge_reports([]).total == 0
