"""
Tests for src/excel/reader.py
"""

import pandas as pd
import pytest
from pathlib import Path
from openpyxl import Workbook

from src.excel.reader import ExcelReader
from src.exceptions import ExcelError, ExcelStructureError


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def excel_with_sheets(tmp_path) -> Path:
    """Excel файл с несколькими листами разных типов."""
    wb = Workbook()
    # Основной лист (content)
    ws = wb.active
    ws.title = "posts"
    ws.append(["id", "action", "title", "slug", "categories"])
    ws.append(["1", "skip", "Hello", "hello", "News"])
    ws.append(["2", "update", "World", "world", "Tech"])
    ws.append(["3", "add", "New Post", "new-post", ""])

    # Лист таксономии
    ws2 = wb.create_sheet("categories")
    ws2.append(["id", "action", "name", "slug"])
    ws2.append(["3", "skip", "News", "news"])

    # Лист медиа
    ws3 = wb.create_sheet("media")
    ws3.append(["id", "action", "alt_text"])
    ws3.append(["99", "skip", "Photo"])

    # Служебный лист
    ws4 = wb.create_sheet("options")
    ws4.append(["key", "value"])

    path = tmp_path / "test.xlsx"
    wb.save(path)
    return path


# ── ExcelReader.classify_sheet() ─────────────────────────────────────

class TestClassifySheet:
    def test_content_sheet(self):
        r = ExcelReader(Path("."))  # path не важен для classify_sheet
        assert r.classify_sheet("posts") == "content"
        assert r.classify_sheet("pages") == "content"
        assert r.classify_sheet("products") == "content"

    def test_taxonomy_sheet(self):
        r = ExcelReader(Path("."))
        assert r.classify_sheet("categories") == "taxonomy"
        assert r.classify_sheet("tags") == "taxonomy"
        assert r.classify_sheet("genre-cat") == "taxonomy"

    def test_media_sheet(self):
        r = ExcelReader(Path("."))
        assert r.classify_sheet("media") == "media"

    def test_options_sheet(self):
        r = ExcelReader(Path("."))
        assert r.classify_sheet("options") == "options"

    def test_profile_sheet(self):
        r = ExcelReader(Path("."))
        assert r.classify_sheet("profile") == "profile"


# ── ExcelReader.read_sheet() ─────────────────────────────────────────

class TestReadSheet:
    def test_read_basic_sheet(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
        assert len(df) == 3
        assert list(df.columns) == ["id", "action", "title", "slug", "categories"]

    def test_data_as_strings(self, excel_with_sheets):
        """По умолчанию все данные читаются как строки."""
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
        assert df["id"].dtype == object  # строка
        assert df.loc[0, "id"] == "1"

    def test_fillna_empty_string(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
        assert df.loc[2, "categories"] == ""  # NaN заменён на ""

    def test_missing_sheet_raises(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            with pytest.raises(ExcelError):
                r.read_sheet("nonexistent_sheet")

    def test_caching(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df1 = r.read_sheet("posts")
            df2 = r.read_sheet("posts")
        # Разные объекты (copy), но одинаковые данные
        assert df1.equals(df2)


# ── ExcelReader.get_sheets_by_type() ─────────────────────────────────

class TestGetSheetsByType:
    def test_classifies_all_sheets(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            by_type = r.get_sheets_by_type()
        assert "posts" in by_type["content"]
        assert "categories" in by_type["taxonomy"]
        assert "media" in by_type["media"]
        assert "options" in by_type["options"]


# ── ExcelReader.filter_by_action() ───────────────────────────────────

class TestFilterByAction:
    def test_filter_add(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
        result = ExcelReader(excel_with_sheets).filter_by_action(df, "add")
        assert len(result) == 1
        assert result.iloc[0]["title"] == "New Post"

    def test_filter_update(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
        result = ExcelReader(excel_with_sheets).filter_by_action(df, "update")
        assert len(result) == 1

    def test_empty_result_for_unknown_action(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
        result = ExcelReader(excel_with_sheets).filter_by_action(df, "nonexistent")
        assert len(result) == 0


# ── ExcelReader.validate_sheet_structure() ───────────────────────────

class TestValidateSheetStructure:
    def test_valid_structure(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            assert r.validate_sheet_structure("posts", ["id", "action", "title"]) is True

    def test_missing_column_raises(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            with pytest.raises(ExcelStructureError):
                r.validate_sheet_structure("posts", ["id", "missing_col"])


# ── ExcelReader.check_duplicate_ids() ────────────────────────────────

class TestCheckDuplicateIds:
    def test_no_duplicates(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
            dups = r.check_duplicate_ids(df, "posts")
        assert dups == []

    def test_detects_duplicates(self, tmp_path):
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "title"])
        ws.append(["1", "skip", "A"])
        ws.append(["1", "skip", "B"])  # duplicate
        ws.append(["2", "skip", "C"])
        path = tmp_path / "dup.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
            dups = r.check_duplicate_ids(df, "posts")
        assert "1" in dups


# ── ExcelReader.validate_actions() ───────────────────────────────────

class TestValidateActions:
    def test_all_valid_actions(self, excel_with_sheets):
        with ExcelReader(excel_with_sheets) as r:
            df = r.read_sheet("posts")
            invalid = r.validate_actions(df, "posts")
        assert invalid == []

    def test_detects_invalid_action(self, tmp_path):
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "title"])
        ws.append(["1", "add", "A"])
        ws.append(["2", "badaction", "B"])
        path = tmp_path / "invalid.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
            invalid = r.validate_actions(df, "posts")
        assert "badaction" in invalid
