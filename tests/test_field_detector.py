"""
Tests for src/mappers/field_detector.py
"""

import pytest

from src.mappers.field_detector import FieldDetector
from src.mappers.value_converter import convert_acf_value


# ── FieldDetector.analyze() ─────────────────────────────────────────

class TestFieldDetectorAnalyze:
    def test_system_columns(self):
        fd = FieldDetector(["id", "action", "type", "title"])
        result = fd.analyze()
        assert "id" in result["system"]
        assert "action" in result["system"]
        assert "type" in result["system"]
        assert "title" in result["content"]

    def test_acf_prefix(self):
        fd = FieldDetector(["acf_price", "acf_color", "title"])
        result = fd.analyze()
        assert result["acf"] == ["price", "color"]
        assert result["content"] == ["title"]

    def test_taxonomy_columns(self):
        fd = FieldDetector(["categories", "tags", "genre-cat", "title"])
        result = fd.analyze()
        assert "categories" in result["taxonomies"]
        assert "tags" in result["taxonomies"]
        assert "genre-cat" in result["taxonomies"]

    def test_content_fields(self):
        fd = FieldDetector(["title", "content", "slug", "status"])
        result = fd.analyze()
        assert result["content"] == ["title", "content", "slug", "status"]

    def test_mixed_columns(self):
        cols = ["id", "action", "title", "acf_weight", "categories", "brand-cat", "slug"]
        fd = FieldDetector(cols)
        r = fd.analyze()
        assert set(r["system"]) == {"id", "action"}
        assert r["acf"] == ["weight"]
        assert set(r["taxonomies"]) == {"categories", "brand-cat"}
        assert set(r["content"]) == {"title", "slug"}

    def test_caching(self):
        fd = FieldDetector(["title", "id"])
        r1 = fd.analyze()
        r2 = fd.analyze()
        assert r1 is r2  # same object, cached

    def test_empty_columns(self):
        fd = FieldDetector([])
        r = fd.analyze()
        assert r == {"system": [], "acf": [], "taxonomies": [], "content": []}


# ── convert_acf_value() ─────────────────────────────────────────────
# (перенесено из FieldDetector в src/mappers/value_converter.py)

class TestConvertAcfValue:
    def test_int_passthrough(self):
        assert convert_acf_value(42) == 42

    def test_float_passthrough(self):
        assert convert_acf_value(3.14) == 3.14

    def test_float_integer_converts_to_int(self):
        assert convert_acf_value(5.0) == 5
        assert isinstance(convert_acf_value(5.0), int)

    def test_string_int(self):
        assert convert_acf_value("123") == 123

    def test_string_float(self):
        assert convert_acf_value("3.14") == 3.14

    def test_string_float_integer(self):
        assert convert_acf_value("5.0") == 5
        assert isinstance(convert_acf_value("5.0"), int)

    def test_date_yyyy_mm_dd(self):
        assert convert_acf_value("2020-04-24") == "20200424"

    def test_date_yyyy_mm_dd_hms(self):
        assert convert_acf_value("2020-04-24 00:00:00") == "20200424"

    def test_date_dd_mm_yyyy_dot(self):
        assert convert_acf_value("24.04.2020") == "20200424"

    def test_date_dd_mm_yyyy_slash(self):
        assert convert_acf_value("24/04/2020") == "20200424"

    def test_acf_date_yyyymmdd_passthrough(self):
        assert convert_acf_value("20200424") == "20200424"

    def test_boolean_true(self):
        for val in ("true", "True", "yes", "Yes", "1"):
            # "1" will be parsed as int 1, not bool True
            result = convert_acf_value(val)
            assert result is True or result == 1

    def test_boolean_false(self):
        for val in ("false", "False", "no", "No", "0"):
            result = convert_acf_value(val)
            assert result is False or result == 0

    def test_plain_string(self):
        assert convert_acf_value("hello world") == "hello world"

    def test_none_value(self):
        import pandas as pd
        assert convert_acf_value(pd.NA) is None

    def test_whitespace_stripped(self):
        assert convert_acf_value("  42  ") == 42
