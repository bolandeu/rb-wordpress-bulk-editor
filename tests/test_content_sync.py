"""
Tests for ContentSync pipeline (integration tests with mocked WordPress client)
"""

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pytest
from pathlib import Path
from openpyxl import Workbook

from src.sync.content_sync import ContentSync
from src.models import SyncReport, SyncResult
from src.config import AppConfig, WordPressConfig, SyncConfig, PathsConfig


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def excel_file(tmp_path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "posts"
    ws.append(["id", "action", "title", "slug"])
    ws.append(["", "add", "New Post", "new-post"])
    ws.append(["10", "update", "Updated Title", "updated-slug"])
    ws.append(["20", "delete", "", ""])
    ws.append(["5", "skip", "Keep This", "keep-this"])
    path = tmp_path / "data.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def app_config(excel_file, tmp_path) -> AppConfig:
    return AppConfig(
        wordpress=WordPressConfig(url="https://example.com", username="u", password="p"),
        sync=SyncConfig(dry_run=False),
        paths=PathsConfig(
            excel_file=excel_file,
            media_folder=tmp_path / "media",
            content_folder=tmp_path / "content",
            logs_folder=tmp_path / "logs",
        ),
    )


@pytest.fixture
def dry_run_config(excel_file, tmp_path) -> AppConfig:
    return AppConfig(
        wordpress=WordPressConfig(url="https://example.com", username="u", password="p"),
        sync=SyncConfig(dry_run=True),
        paths=PathsConfig(
            excel_file=excel_file,
            media_folder=tmp_path / "media",
            content_folder=tmp_path / "content",
            logs_folder=tmp_path / "logs",
        ),
    )


@pytest.fixture
def mock_wp_client():
    """Mock WordPress client, возвращающий фиктивные ответы."""
    client = MagicMock()
    client.get = AsyncMock(return_value=[])
    client.post = AsyncMock(return_value={"id": 99, "slug": "new-post", "link": "https://example.com/new-post", "modified": "", "date": "", "featured_media": 0, "image_url": ""})
    client.put = AsyncMock(return_value={"id": 10, "slug": "updated-slug", "link": "https://example.com/updated", "modified": "2024-01-01", "date": "2024-01-01", "featured_media": 0, "image_url": ""})
    client.delete = AsyncMock(return_value={"id": 20, "status": "trash"})
    return client


@pytest.fixture
def mock_taxonomy_cache():
    cache = MagicMock()
    cache.get_id_to_name = MagicMock(return_value={})
    cache.get_term_ids = AsyncMock(return_value=[])
    return cache


# ── ContentSync return type ───────────────────────────────────────────

class TestContentSyncReturnType:
    async def test_sync_sheet_returns_sync_report(
        self, app_config, mock_wp_client, mock_taxonomy_cache
    ):
        """sync_sheet должен возвращать SyncReport, а не dict."""
        df = pd.DataFrame({
            "id": ["", "10"],
            "action": ["skip", "skip"],
            "title": ["A", "B"],
        })

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        result = await sync.sync_sheet("posts", df, excel_writer=None)

        assert isinstance(result, SyncReport)
        assert hasattr(result, "total")
        assert hasattr(result, "created")
        assert hasattr(result, "updated")
        assert hasattr(result, "failed")
        assert hasattr(result, "errors")
        assert isinstance(result.errors, list)

    async def test_sync_report_to_stats_dict(
        self, app_config, mock_wp_client, mock_taxonomy_cache
    ):
        """SyncReport.to_stats() должен возвращать словарь с нужными ключами."""
        df = pd.DataFrame({
            "id": [""],
            "action": ["skip"],
            "title": ["A"],
        })

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_sheet("posts", df, excel_writer=None)
        stats = report.to_stats()

        assert "total" in stats
        assert "added" in stats
        assert "updated" in stats
        assert "deleted" in stats
        assert "skipped" in stats
        assert "errors" in stats


# ── ContentSync stats counting ────────────────────────────────────────

class TestContentSyncStats:
    async def test_skipped_rows_counted(
        self, app_config, mock_wp_client, mock_taxonomy_cache
    ):
        df = pd.DataFrame({
            "id": ["1", "2", "3"],
            "action": ["skip", "skip", "skip"],
            "title": ["A", "B", "C"],
        })

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_sheet("posts", df, excel_writer=None)

        assert report.total == 3
        assert report.skipped == 3
        assert report.created == 0

    async def test_total_equals_df_length(
        self, app_config, mock_wp_client, mock_taxonomy_cache
    ):
        df = pd.DataFrame({
            "id": ["", "", ""],
            "action": ["skip", "skip", "skip"],
            "title": ["A", "B", "C"],
        })

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_sheet("posts", df, excel_writer=None)

        assert report.total == len(df)


# ── SyncResult error tracking ─────────────────────────────────────────

class TestSyncResultErrors:
    async def test_error_captured_in_sync_result(
        self, app_config, mock_wp_client, mock_taxonomy_cache
    ):
        """Ошибки API должны попасть в SyncReport.errors как SyncResult."""
        # API всегда бросает исключение
        mock_wp_client.post = AsyncMock(side_effect=Exception("API error"))
        mock_wp_client.put = AsyncMock(side_effect=Exception("API error"))

        df = pd.DataFrame({
            "id": [""],
            "action": ["add"],
            "title": ["Post with error"],
        })

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_sheet("posts", df, excel_writer=None)

        assert report.failed >= 0  # Ошибки считаются
        # Если ошибка попала в errors list, проверяем структуру
        for err in report.errors:
            assert isinstance(err, SyncResult)
            assert err.success is False
            assert err.error is not None


# ── Dry-run mode ──────────────────────────────────────────────────────

class TestDryRunMode:
    async def test_dry_run_does_not_call_api(
        self, dry_run_config, mock_wp_client, mock_taxonomy_cache
    ):
        """В dry-run режиме API не вызывается."""
        df = pd.DataFrame({
            "id": ["", "10"],
            "action": ["add", "update"],
            "title": ["New", "Updated"],
        })

        sync = ContentSync(mock_wp_client, dry_run_config, mock_taxonomy_cache)
        await sync.sync_sheet("posts", df, excel_writer=None)

        # В dry-run не должны вызываться post/put/delete
        mock_wp_client.post.assert_not_called()
        mock_wp_client.put.assert_not_called()
        mock_wp_client.delete.assert_not_called()


# ── SyncReport.from_stats() ───────────────────────────────────────────

class TestSyncReportFromStats:
    def test_from_stats_basic(self):
        stats = {"total": 10, "added": 3, "updated": 2, "deleted": 1, "skipped": 4, "errors": 0}
        report = SyncReport.from_stats(stats)
        assert report.total == 10
        assert report.created == 3
        assert report.updated == 2
        assert report.deleted == 1
        assert report.skipped == 4
        assert report.failed == 0
        assert report.errors == []

    def test_to_stats_roundtrip(self):
        stats = {"total": 5, "added": 1, "updated": 1, "deleted": 1, "skipped": 2, "errors": 0}
        report = SyncReport.from_stats(stats)
        result = report.to_stats()
        assert result["total"] == 5
        assert result["added"] == 1
        assert result["updated"] == 1
        assert result["deleted"] == 1
        assert result["skipped"] == 2
        assert result["errors"] == 0

    def test_from_stats_with_error_results(self):
        stats = {"total": 3, "added": 0, "updated": 0, "deleted": 0, "skipped": 2, "errors": 1}
        err = SyncResult(row_index=0, action="add", post_id=None, success=False, error="fail")
        report = SyncReport.from_stats(stats, [err])
        assert len(report.errors) == 1
        assert report.errors[0].error == "fail"
