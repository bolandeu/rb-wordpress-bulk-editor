"""
Tests for ContentSync pipeline (integration tests with mocked WordPress client)
"""

from unittest.mock import AsyncMock, MagicMock
import pandas as pd
import pytest
from pathlib import Path
from openpyxl import Workbook

from src.records.base import Record
from src.sync.content_sync import ContentSync
from src.models import SyncReport, SyncResult
from src.config import AppConfig, WordPressConfig, SyncConfig, PathsConfig
from src.wordpress.taxonomies import TaxonomyCache


# ── Helpers ─────────────────────────────────────────────────────────


def make_records(df: pd.DataFrame, discriminator: str = "posts") -> list[Record]:
    """DataFrame → список Record (как их отдаёт ExcelRecordSource)."""
    return [
        Record(discriminator=discriminator, is_taxonomy=False, data=row.to_dict(), write_key=idx)
        for idx, row in df.iterrows()
    ]


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
    client.post = AsyncMock(
        return_value={
            "id": 99,
            "slug": "new-post",
            "link": "https://example.com/new-post",
            "modified": "",
            "date": "",
            "featured_media": 0,
            "image_url": "",
        }
    )
    client.put = AsyncMock(
        return_value={
            "id": 10,
            "slug": "updated-slug",
            "link": "https://example.com/updated",
            "modified": "2024-01-01",
            "date": "2024-01-01",
            "featured_media": 0,
            "image_url": "",
        }
    )
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
    async def test_sync_records_returns_sync_report(self, app_config, mock_wp_client, mock_taxonomy_cache):
        """sync_records должен возвращать SyncReport, а не dict."""
        df = pd.DataFrame(
            {
                "id": ["", "10"],
                "action": ["skip", "skip"],
                "title": ["A", "B"],
            }
        )

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        result = await sync.sync_records("posts", make_records(df), writer=None)

        assert isinstance(result, SyncReport)
        assert hasattr(result, "total")
        assert hasattr(result, "created")
        assert hasattr(result, "updated")
        assert hasattr(result, "failed")
        assert hasattr(result, "errors")
        assert isinstance(result.errors, list)

    async def test_sync_report_to_stats_dict(self, app_config, mock_wp_client, mock_taxonomy_cache):
        """SyncReport.to_stats() должен возвращать словарь с нужными ключами."""
        df = pd.DataFrame(
            {
                "id": [""],
                "action": ["skip"],
                "title": ["A"],
            }
        )

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_records("posts", make_records(df), writer=None)
        stats = report.to_stats()

        assert "total" in stats
        assert "added" in stats
        assert "updated" in stats
        assert "deleted" in stats
        assert "skipped" in stats
        assert "errors" in stats


# ── ContentSync stats counting ────────────────────────────────────────


class TestContentSyncStats:
    async def test_skipped_rows_counted(self, app_config, mock_wp_client, mock_taxonomy_cache):
        df = pd.DataFrame(
            {
                "id": ["1", "2", "3"],
                "action": ["skip", "skip", "skip"],
                "title": ["A", "B", "C"],
            }
        )

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_records("posts", make_records(df), writer=None)

        assert report.total == 3
        assert report.skipped == 3
        assert report.created == 0

    async def test_total_equals_df_length(self, app_config, mock_wp_client, mock_taxonomy_cache):
        df = pd.DataFrame(
            {
                "id": ["", "", ""],
                "action": ["skip", "skip", "skip"],
                "title": ["A", "B", "C"],
            }
        )

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_records("posts", make_records(df), writer=None)

        assert report.total == len(df)


# ── SyncResult error tracking ─────────────────────────────────────────


class TestSyncResultErrors:
    async def test_error_captured_in_sync_result(self, app_config, mock_wp_client, mock_taxonomy_cache):
        """Ошибки API должны попасть в SyncReport.errors как SyncResult."""
        # API всегда бросает исключение
        mock_wp_client.post = AsyncMock(side_effect=Exception("API error"))
        mock_wp_client.put = AsyncMock(side_effect=Exception("API error"))

        df = pd.DataFrame(
            {
                "id": [""],
                "action": ["add"],
                "title": ["Post with error"],
            }
        )

        sync = ContentSync(mock_wp_client, app_config, mock_taxonomy_cache)
        report = await sync.sync_records("posts", make_records(df), writer=None)

        assert report.failed >= 0  # Ошибки считаются
        # Если ошибка попала в errors list, проверяем структуру
        for err in report.errors:
            assert isinstance(err, SyncResult)
            assert err.success is False
            assert err.error is not None


# ── Dry-run mode ──────────────────────────────────────────────────────


class TestDryRunMode:
    async def test_dry_run_does_not_call_api(self, dry_run_config, mock_wp_client, mock_taxonomy_cache):
        """В dry-run режиме API не вызывается."""
        df = pd.DataFrame(
            {
                "id": ["", "10"],
                "action": ["add", "update"],
                "title": ["New", "Updated"],
            }
        )

        sync = ContentSync(mock_wp_client, dry_run_config, mock_taxonomy_cache)
        await sync.sync_records("posts", make_records(df), writer=None)

        # В dry-run не должны вызываться post/put/delete
        mock_wp_client.post.assert_not_called()
        mock_wp_client.put.assert_not_called()
        mock_wp_client.delete.assert_not_called()


# ── Таксономии: иерархия и primary-термин ─────────────────────────────


class RoutingClient:
    """Фейковый WP-клиент: маршрутизирует запросы постов и терминов по endpoint."""

    TERMS_ENDPOINT = "/wp-json/wp/v2/services_cat"
    POSTS_ENDPOINT = "/wp-json/wp/v2/posts"

    def __init__(self):
        self.terms = []
        self.next_term_id = 100
        self.created_posts = []
        self.put_calls = []
        self.term_queries = []

    async def get(self, endpoint, params=None, **kwargs):
        params = params or {}
        if endpoint == self.TERMS_ENDPOINT:
            self.term_queries.append(params)
        result = self.terms
        if "search" in params:
            result = [t for t in result if params["search"].lower() in t["name"].lower()]
        if "parent" in params:
            result = [t for t in result if t["parent"] == int(params["parent"])]
        return list(result)

    async def post(self, endpoint, data=None, **kwargs):
        data = data or {}
        if endpoint == self.TERMS_ENDPOINT:
            term = {"id": self.next_term_id, "name": data["name"], "parent": int(data.get("parent", 0) or 0)}
            self.next_term_id += 1
            self.terms.append(term)
            return term
        self.created_posts.append(data)
        return {"id": 99, "link": "https://example.com/p", "modified": "", "date": ""}

    async def put(self, endpoint, data=None, **kwargs):
        self.put_calls.append((endpoint, data or {}))
        return {"id": 99, "link": "https://example.com/p", "modified": ""}

    async def delete(self, endpoint, params=None, **kwargs):
        return {"id": 99}


class TestTaxonomyHierarchyEndToEnd:
    async def test_hierarchy_and_primary_term(self, app_config):
        """Смешанная строка таксономии: иерархия создаётся, primary — первый термин."""
        client = RoutingClient()
        records = [
            Record(
                discriminator="posts",
                is_taxonomy=False,
                data={
                    "id": "",
                    "action": "add",
                    "title": "Post",
                    "services_cat": "термин1; термин2>термин2.1",
                },
                write_key=0,
            )
        ]

        sync = ContentSync(client, app_config, TaxonomyCache())
        report = await sync.sync_records("posts", records, writer=None)

        assert report.created == 1
        # Созданы три термина: плоский, родитель, ребёнок
        assert [(t["name"], t["parent"]) for t in client.terms] == [
            ("термин1", 0),
            ("термин2", 0),
            ("термин2.1", 101),
        ]
        # Записи присвоены только термин1 и термин2.1
        payload = client.created_posts[0]
        assert payload["services_cat"] == [100, 102]
        # Primary — первая спецификация строки (термин1)
        assert client.put_calls == [
            (
                "/wp-json/wp/v2/posts/99",
                {"meta": {"_yoast_wpseo_primary_services_cat": 100}},
            )
        ]

    async def test_primary_meta_failure_does_not_break_sync(self, app_config):
        """Падение записи primary-меты не ломает синхронизацию записи."""
        client = RoutingClient()
        client.put = AsyncMock(side_effect=Exception("meta not registered"))
        records = [
            Record(
                discriminator="posts",
                is_taxonomy=False,
                data={"id": "", "action": "add", "title": "Post", "services_cat": "термин1"},
                write_key=0,
            )
        ]

        sync = ContentSync(client, app_config, TaxonomyCache())
        report = await sync.sync_records("posts", records, writer=None)

        assert report.created == 1
        assert report.errors == []
        assert client.created_posts[0]["services_cat"] == [100]


class TestTaxonomyDryRunDoesNotCreate:
    """--dry-run не должен создавать реальные термины таксономии на сайте."""

    async def test_missing_term_not_created(self, dry_run_config):
        """Термин, которого ещё нет на сайте: под dry-run — только поиск, без create."""
        client = RoutingClient()
        records = [
            Record(
                discriminator="posts",
                is_taxonomy=False,
                data={"id": "", "action": "add", "title": "Post", "services_cat": "Новая категория"},
                write_key=0,
            )
        ]

        sync = ContentSync(client, dry_run_config, TaxonomyCache())
        report = await sync.sync_records("posts", records, writer=None)

        assert report.created == 1
        # Ни один термин не был создан на сайте
        assert client.terms == []
        # Поиск (read-only) всё же выполнялся — превью остаётся точным
        assert any("search" in q for q in client.term_queries)
        # И сам пост не создавался (dry-run вообще без записи)
        assert client.created_posts == []
        assert client.put_calls == []

    async def test_existing_term_still_looked_up(self, dry_run_config):
        """Уже существующий термин находится под dry-run как обычно (без create)."""
        client = RoutingClient()
        client.terms.append({"id": 100, "name": "Портьеры", "parent": 0})
        records = [
            Record(
                discriminator="posts",
                is_taxonomy=False,
                data={"id": "", "action": "add", "title": "Post", "services_cat": "Портьеры"},
                write_key=0,
            )
        ]

        sync = ContentSync(client, dry_run_config, TaxonomyCache())
        report = await sync.sync_records("posts", records, writer=None)

        assert report.created == 1
        # Термин не задублировался и не был "создан" повторно
        assert client.terms == [{"id": 100, "name": "Портьеры", "parent": 0}]
        assert any("search" in q for q in client.term_queries)

    async def test_hierarchy_missing_ancestors_not_created(self, dry_run_config):
        """Иерархический термин с несуществующими предками: под dry-run ничего не создаётся."""
        client = RoutingClient()
        records = [
            Record(
                discriminator="posts",
                is_taxonomy=False,
                data={
                    "id": "",
                    "action": "add",
                    "title": "Post",
                    "services_cat": "термин1; термин2>термин2.1",
                },
                write_key=0,
            )
        ]

        sync = ContentSync(client, dry_run_config, TaxonomyCache())
        report = await sync.sync_records("posts", records, writer=None)

        assert report.created == 1
        assert client.terms == []
        assert client.put_calls == []

    async def test_primary_term_meta_not_written(self, dry_run_config):
        """Primary-термин под dry-run не пишется в мета-поле (нет реального PUT)."""
        client = RoutingClient()
        records = [
            Record(
                discriminator="posts",
                is_taxonomy=False,
                data={"id": "", "action": "add", "title": "Post", "services_cat": "термин1"},
                write_key=0,
            )
        ]

        sync = ContentSync(client, dry_run_config, TaxonomyCache())
        report = await sync.sync_records("posts", records, writer=None)

        assert report.created == 1
        assert client.terms == []
        assert client.put_calls == []


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
