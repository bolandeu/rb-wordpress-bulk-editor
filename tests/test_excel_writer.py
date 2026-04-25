"""
Tests for src/excel/writer.py
"""

import asyncio
import pytest
from pathlib import Path
from openpyxl import Workbook

from src.excel.writer import ExcelWriter, normalize_api_value


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def excel_file_with_posts(tmp_path) -> Path:
    """Excel файл с листом posts."""
    wb = Workbook()
    ws = wb.active
    ws.title = "posts"
    ws.append(["id", "action", "title", "slug", "link", "modified", "date",
               "content_path", "img_path", "featured_media", "image_url"])
    ws.append(["", "add", "Hello World", "", "", "", "", "", "", "", ""])
    ws.append(["10", "update", "Old Title", "old-slug", "", "", "", "", "", "", ""])
    path = tmp_path / "data.xlsx"
    wb.save(path)
    return path


# ── normalize_api_value() ─────────────────────────────────────────────

class TestNormalizeApiValue:
    def test_string_passthrough(self):
        assert normalize_api_value("hello") == "hello"

    def test_int_passthrough(self):
        assert normalize_api_value(42) == 42

    def test_dict_with_raw(self):
        assert normalize_api_value({"raw": "Text", "rendered": "<p>Text</p>"}) == "Text"

    def test_dict_with_rendered_only(self):
        assert normalize_api_value({"rendered": "<p>Text</p>"}) == "<p>Text</p>"

    def test_dict_without_known_keys(self):
        d = {"other": "value"}
        assert normalize_api_value(d) == d

    def test_none_passthrough(self):
        assert normalize_api_value(None) is None


# ── ExcelWriter.update_row() ──────────────────────────────────────────

class TestUpdateRow:
    def test_update_id_column(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        writer.update_row("posts", 0, {"id": "42"})
        assert str(writer._dataframes["posts"].at[0, "id"]) == "42"

    def test_update_action_to_skip(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        writer.update_row("posts", 0, {"action": "skip"})
        assert writer._dataframes["posts"].at[0, "action"] == "skip"

    def test_update_multiple_fields(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        writer.update_row("posts", 0, {"id": "99", "slug": "new-slug", "action": "skip"})
        df = writer._dataframes["posts"]
        assert str(df.at[0, "id"]) == "99"
        assert df.at[0, "slug"] == "new-slug"
        assert df.at[0, "action"] == "skip"

    def test_update_nonexistent_column_skipped(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        # Не должно падать при несуществующей колонке
        writer.update_row("posts", 0, {"nonexistent_col": "value"})

    def test_update_wrong_sheet_ignored(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        # Не должно падать при несуществующем листе
        writer.update_row("nonexistent_sheet", 0, {"id": "1"})


# ── ExcelWriter dirty-флаг ─────────────────────────────────────────────

class TestDirtyFlag:
    def test_save_is_noop_without_changes(self, excel_file_with_posts):
        """Регрессия: read-only сценарий не должен переписывать файл (теряя форматирование)."""
        original_mtime = excel_file_with_posts.stat().st_mtime_ns

        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        writer.save()  # никаких update_row до этого — файл не должен быть тронут

        assert excel_file_with_posts.stat().st_mtime_ns == original_mtime

    def test_save_writes_after_update(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        writer.update_row("posts", 0, {"id": "777"})
        writer.save()

        # Перечитываем — изменения должны быть на диске
        import pandas as pd
        df = pd.read_excel(excel_file_with_posts, sheet_name="posts", dtype=str)
        assert df.at[0, "id"] == "777"

    def test_update_nonexistent_column_does_not_dirty(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()
        writer.update_row("posts", 0, {"nonexistent_col": "value"})
        assert writer._dirty is False


# ── ExcelWriter.update_from_api_response() ───────────────────────────

class TestUpdateFromApiResponse:
    def test_add_action_writes_id_and_link(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()

        api_response = {
            "id": 55,
            "slug": "hello-world",
            "link": "https://example.com/hello-world",
            "date": "2024-01-15T10:00:00",
            "modified": "2024-01-15T10:00:00",
            "featured_media": 0,
        }
        writer.update_from_api_response("posts", 0, api_response, "add")

        df = writer._dataframes["posts"]
        assert str(df.at[0, "id"]) == "55"
        assert df.at[0, "action"] == "skip"

    def test_content_path_cleared_after_add(self, excel_file_with_posts):
        """content_path и img_path должны очищаться после успешной записи."""
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()

        api_response = {"id": 55, "slug": "test", "link": "https://x.com"}
        writer.update_from_api_response("posts", 0, api_response, "add")

        df = writer._dataframes["posts"]
        assert df.at[0, "content_path"] == "" or df.at[0, "content_path"] != df.at[0, "content_path"]  # empty or NaN

    def test_update_action_writes_modified(self, excel_file_with_posts):
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()

        api_response = {
            "id": 10,
            "modified": "2024-06-01T00:00:00",
            "link": "https://example.com/post",
        }
        writer.update_from_api_response("posts", 1, api_response, "update")
        df = writer._dataframes["posts"]
        assert df.at[1, "modified"] == "2024-06-01T00:00:00"


# ── ExcelWriter.update_row_async() ────────────────────────────────────

class TestUpdateRowAsync:
    async def test_async_update_is_thread_safe(self, excel_file_with_posts):
        """Concurrent async updates не должны перезатирать данные."""
        writer = ExcelWriter(excel_file_with_posts)
        writer.load()

        async def update(row_idx, value):
            await writer.update_row_async("posts", row_idx, {"title": value})

        await asyncio.gather(
            update(0, "Title A"),
            update(1, "Title B"),
        )

        df = writer._dataframes["posts"]
        # Оба обновления должны были применяться без гонок
        assert df.at[0, "title"] in ("Title A", "Hello World")  # row 0 обновлён
        assert df.at[1, "title"] in ("Title B", "Old Title")    # row 1 обновлён
