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
        assert pd.api.types.is_string_dtype(df["id"])  # object или StringDtype
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

    def test_drops_columns_without_header(self, tmp_path):
        """Колонки с пустым заголовком в шапке (pandas: `Unnamed: N`) игнорируются."""
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        # Шапка с дыркой между title и slug + хвостовой пустой заголовок
        ws.append(["id", "action", "title", None, "slug", None])
        ws.append(["1", "skip", "Hello", "junk-mid", "hello", "junk-tail"])
        path = tmp_path / "empty_headers.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
        assert list(df.columns) == ["id", "action", "title", "slug"]
        assert "junk-mid" not in df.values
        assert "junk-tail" not in df.values

    def test_numeric_headers_become_strings(self, tmp_path):
        """Числовой заголовок (например, 301 на листе редиректов) — строка: имена полей всегда str."""
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "title", 301])
        ws.append(["1", "skip", "Hello", "/new"])
        path = tmp_path / "numeric_header.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
        assert list(df.columns) == ["id", "action", "title", "301"]

    def test_drops_fully_empty_rows(self, tmp_path):
        """Полностью пустые строки в середине/конце листа отбрасываются."""
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "title"])
        ws.append(["1", "skip", "Hello"])
        ws.append([None, None, None])  # полностью пустая
        ws.append(["2", "add", "World"])
        ws.append(["", "", ""])  # пустая через ""
        path = tmp_path / "empty_rows.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
        assert df["title"].tolist() == ["Hello", "World"]

    def test_drops_rows_without_minimal_fields(self, tmp_path):
        """Строки без action И title/name (всё пусто) отбрасываются как мусорные."""
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "title", "slug"])
        ws.append(["1", "skip", "Hello", "hello"])
        ws.append(["99", "", "", "orphan-slug"])  # есть id+slug, но нет action и title — мусор
        ws.append(["2", "delete", "", ""])  # action есть → keep
        ws.append(["", "", "Just title", ""])  # title есть → keep
        path = tmp_path / "minimal.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
        ids = df["id"].tolist()
        assert "1" in ids and "2" in ids
        assert "99" not in ids
        # строка без id, но с title — сохранена
        assert "Just title" in df["title"].tolist()

    def test_taxonomy_keeps_rows_with_name(self, tmp_path):
        """Для таксономии минимум — `name` (title может отсутствовать)."""
        wb = Workbook()
        ws = wb.active
        ws.title = "categories"
        ws.append(["id", "action", "name", "slug"])
        ws.append(["1", "skip", "News", "news"])
        ws.append(["", "", "Tech", ""])  # name есть → keep
        ws.append(["", "", "", ""])  # пусто → drop
        path = tmp_path / "tax.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("categories")
        assert df["name"].tolist() == ["News", "Tech"]

    def test_options_sheet_only_drops_fully_empty(self, tmp_path):
        """Лист без action/title/name (например options) фильтрует только полностью пустые."""
        wb = Workbook()
        ws = wb.active
        ws.title = "options"
        ws.append(["key", "value"])
        ws.append(["site_title", "MySite"])
        ws.append(["", ""])  # пустая → drop
        ws.append(["timezone", ""])  # есть key → keep (нет правила для минимума)
        path = tmp_path / "opts.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("options")
        assert df["key"].tolist() == ["site_title", "timezone"]

    def test_drops_double_underscore_columns(self, tmp_path):
        """Колонки с префиксом `__` игнорируются как служебные."""
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "__note", "title", "__draft_slug"])
        ws.append(["1", "skip", "internal note", "Hello", "draft-hello"])
        path = tmp_path / "underscore.xlsx"
        wb.save(path)

        with ExcelReader(path) as r:
            df = r.read_sheet("posts")
        assert list(df.columns) == ["id", "action", "title"]
        assert "__note" not in df.columns
        assert "__draft_slug" not in df.columns


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


# ── known_taxonomies override ────────────────────────────────────────


class TestKnownTaxonomiesOverride:
    @pytest.fixture
    def sheets_path(self, tmp_path) -> Path:
        path = tmp_path / "multi.xlsx"
        wb = Workbook()
        wb.remove(wb.active)
        for name in ["posts", "brand", "categories", "random_sheet", "product-cat"]:
            ws = wb.create_sheet(name)
            ws.append(["id", "action"])
        wb.save(path)
        return path

    def test_without_override_brand_is_content(self, sheets_path):
        with ExcelReader(sheets_path) as r:
            by_type = r.get_sheets_by_type()
        assert "brand" in by_type["content"]
        assert "categories" in by_type["taxonomy"]
        assert "product-cat" in by_type["taxonomy"]

    def test_with_override_brand_becomes_taxonomy(self, sheets_path):
        with ExcelReader(sheets_path, known_taxonomies=["brand"]) as r:
            by_type = r.get_sheets_by_type()
        assert "brand" in by_type["taxonomy"]
        assert "categories" in by_type["taxonomy"]
        assert "posts" in by_type["content"]

    def test_override_does_not_break_fallback(self, sheets_path):
        with ExcelReader(sheets_path, known_taxonomies=["brand"]) as r:
            by_type = r.get_sheets_by_type()
        assert "categories" in by_type["taxonomy"]
        assert "product-cat" in by_type["taxonomy"]
