"""
Tests for src/records/excel.py — обёртки должны вести себя как прямой
ExcelReader/ExcelWriter (регрессия Excel-пути).
"""

import shutil
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from src.excel.reader import ExcelReader
from src.excel.writer import ExcelWriter
from src.records.excel import ExcelRecordSource, ExcelRecordWriter


@pytest.fixture
def posts_file(tmp_path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "posts"
    ws.append(["id", "action", "title", "slug", "link", "modified"])
    ws.append(["", "add", "New Post", "new-post", "", ""])
    ws.append(["10", "update", "Updated Title", "updated-slug", "", ""])
    ws.append(["", "", "", "", "", ""])  # пустая строка — reader её выбрасывает
    ws.append(["5", "skip", "Keep This", "keep-this", "", ""])
    tags = wb.create_sheet("tags")
    tags.append(["id", "action", "name", "slug"])
    tags.append(["", "add", "Новости", "novosti"])
    path = tmp_path / "data.xlsx"
    wb.save(path)
    return path


class TestExcelRecordSource:
    def test_read_matches_reader(self, posts_file):
        with ExcelReader(posts_file) as reader:
            df = reader.read_sheet("posts")
            records = list(ExcelRecordSource(reader, "posts").read())

        assert [r.data for r in records] == [row.to_dict() for _, row in df.iterrows()]
        # write_key — тот самый label строки pandas, по которому пишет ExcelWriter
        assert [r.write_key for r in records] == df.index.tolist()
        # Пропущенная пустая строка => индекс с «дыркой», сбрасывать его нельзя
        assert [r.write_key for r in records] == [0, 1, 3]

    def test_fields_match_columns(self, posts_file):
        with ExcelReader(posts_file) as reader:
            source = ExcelRecordSource(reader, "posts")
            assert source.fields() == reader.read_sheet("posts").columns.tolist()

    def test_discriminator_and_taxonomy_flag(self, posts_file):
        with ExcelReader(posts_file) as reader:
            posts = list(ExcelRecordSource(reader, "posts").read())
            tags = list(ExcelRecordSource(reader, "tags").read())

        assert {r.discriminator for r in posts} == {"posts"}
        assert all(r.is_taxonomy is False for r in posts)
        assert {r.discriminator for r in tags} == {"tags"}
        assert all(r.is_taxonomy is True for r in tags)


class TestExcelRecordWriter:
    def test_writeback_matches_direct_writer(self, posts_file, tmp_path):
        api_response = {
            "id": 99,
            "link": "https://example.com/new-post",
            "modified": "2026-01-01",
            "slug": "new-post",
        }

        direct_file = tmp_path / "direct.xlsx"
        wrapped_file = tmp_path / "wrapped.xlsx"
        shutil.copy(posts_file, direct_file)
        shutil.copy(posts_file, wrapped_file)

        direct = ExcelWriter(direct_file)
        direct.load()
        direct.update_from_api_response("posts", 0, api_response, "add")
        direct.save()

        base = ExcelWriter(wrapped_file)
        base.load()
        ExcelRecordWriter(base, "posts").update_from_result(0, api_response, "add")
        base.save()

        pd.testing.assert_frame_equal(
            pd.read_excel(direct_file, sheet_name="posts", dtype=str),
            pd.read_excel(wrapped_file, sheet_name="posts", dtype=str),
        )

    def test_save_skipped_without_changes(self, posts_file):
        original = posts_file.read_bytes()

        writer = ExcelWriter(posts_file)
        writer.load()
        ExcelRecordWriter(writer, "posts").save()

        assert posts_file.read_bytes() == original
