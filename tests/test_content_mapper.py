"""
Tests for src/mappers/content_mapper.py
"""

import pandas as pd
import pytest

from src.mappers.content_mapper import ContentMapper


# ── Helpers ─────────────────────────────────────────────────────────

def make_row(**kwargs) -> pd.Series:
    """Создать pandas Series из kwargs."""
    return pd.Series(kwargs)


# ── ContentMapper.map_row() ──────────────────────────────────────────

class TestMapRow:
    def test_basic_content_fields(self):
        mapper = ContentMapper(["title", "slug", "status", "id", "action"])
        row = make_row(title="Hello", slug="hello", status="publish", id="", action="add")
        payload = mapper.map_row(row)
        assert payload["title"] == "Hello"
        assert payload["slug"] == "hello"
        assert payload["status"] == "publish"
        # id и action — системные, не попадают в payload
        assert "id" not in payload
        assert "action" not in payload

    def test_empty_value_skipped(self):
        mapper = ContentMapper(["status", "id", "action"])
        row = make_row(status="", id="", action="add")
        payload = mapper.map_row(row)
        assert "status" not in payload

    def test_send_empty_for_seo_fields(self):
        """Поля seo_* должны отправляться даже если пустые."""
        mapper = ContentMapper(["seo_title", "seo_description", "id", "action"])
        row = make_row(seo_title="", seo_description="", id="", action="update")
        payload = mapper.map_row(row)
        assert payload["seo_title"] == ""
        assert payload["seo_description"] == ""

    def test_send_empty_title_field(self):
        """title должен отправляться даже если пустой (для обнуления)."""
        mapper = ContentMapper(["title", "id", "action"])
        row = make_row(title="", id="10", action="update")
        payload = mapper.map_row(row)
        assert "title" in payload
        assert payload["title"] == ""

    def test_acf_fields_mapped(self):
        mapper = ContentMapper(["acf_price", "acf_color", "id", "action"])
        row = make_row(**{"acf_price": "100", "acf_color": "red", "id": "", "action": "add"})
        payload = mapper.map_row(row)
        assert "acf" in payload
        assert payload["acf"]["price"] == 100  # строка "100" → int
        assert payload["acf"]["color"] == "red"

    def test_acf_empty_values_skipped(self):
        mapper = ContentMapper(["acf_price", "id", "action"])
        row = make_row(**{"acf_price": "", "id": "", "action": "add"})
        payload = mapper.map_row(row)
        assert "acf" not in payload

    def test_taxonomy_ids_injected(self):
        mapper = ContentMapper(["title", "categories", "id", "action"])
        row = make_row(title="Post", categories="News", id="", action="add")
        payload = mapper.map_row(row, taxonomy_ids={"categories": [3, 5]})
        assert payload["categories"] == [3, 5]

    def test_featured_media_converted_to_int(self):
        mapper = ContentMapper(["featured_media", "id", "action"])
        row = make_row(**{"featured_media": "99", "id": "", "action": "add"})
        payload = mapper.map_row(row)
        assert payload["featured_media"] == 99
        assert isinstance(payload["featured_media"], int)

    def test_allowed_columns_filter(self):
        mapper = ContentMapper(
            ["title", "slug", "status", "id", "action"],
            allowed_columns=["title"]
        )
        row = make_row(title="Hello", slug="hello", status="publish", id="", action="update")
        payload = mapper.map_row(row)
        assert "title" in payload
        assert "slug" not in payload
        assert "status" not in payload

    def test_allowed_columns_filter_acf(self):
        """Фильтр по acf_ колонкам."""
        mapper = ContentMapper(
            ["acf_price", "acf_color", "id", "action"],
            allowed_columns=["acf_price"]
        )
        row = make_row(**{"acf_price": "100", "acf_color": "red", "id": "", "action": "update"})
        payload = mapper.map_row(row)
        assert "acf" in payload
        assert "price" in payload["acf"]
        assert "color" not in payload["acf"]

    def test_float_value_converts_to_int(self):
        mapper = ContentMapper(["menu_order", "id", "action"])
        row = make_row(**{"menu_order": 5.0, "id": "", "action": "add"})
        payload = mapper.map_row(row)
        assert payload["menu_order"] == 5
        assert isinstance(payload["menu_order"], int)


# ── ContentMapper.validate_row() ─────────────────────────────────────

class TestValidateRow:
    def test_add_requires_title(self):
        mapper = ContentMapper(["title", "id", "action"])
        row = make_row(title="Hello", id="", action="add")
        is_valid, msg = mapper.validate_row(row, "add")
        assert is_valid is True
        assert msg is None

    def test_add_fails_without_title(self):
        mapper = ContentMapper(["title", "id", "action"])
        row = make_row(title="", id="", action="add")
        is_valid, msg = mapper.validate_row(row, "add")
        assert is_valid is False
        assert "title" in msg

    def test_update_requires_id(self):
        mapper = ContentMapper(["title", "id", "action"])
        row = make_row(title="Hello", id="42", action="update")
        is_valid, msg = mapper.validate_row(row, "update")
        assert is_valid is True

    def test_update_fails_without_id(self):
        mapper = ContentMapper(["title", "id", "action"])
        row = make_row(title="Hello", id="", action="update")
        is_valid, msg = mapper.validate_row(row, "update")
        assert is_valid is False
        assert "id" in msg

    def test_delete_requires_id(self):
        mapper = ContentMapper(["id", "action"])
        row = make_row(id="42", action="delete")
        is_valid, msg = mapper.validate_row(row, "delete")
        assert is_valid is True

    def test_skip_has_no_required_fields(self):
        mapper = ContentMapper(["id", "action"])
        row = make_row(id="", action="skip")
        is_valid, msg = mapper.validate_row(row, "skip")
        assert is_valid is True


# ── ContentMapper.extract_media_path() ───────────────────────────────

class TestExtractMediaPath:
    def test_returns_path(self):
        mapper = ContentMapper(["img_path", "id", "action"])
        row = make_row(**{"img_path": "media/photo.jpg", "id": "", "action": "add"})
        assert mapper.extract_media_path(row) == "media/photo.jpg"

    def test_returns_none_when_empty(self):
        mapper = ContentMapper(["img_path", "id", "action"])
        row = make_row(**{"img_path": "", "id": "", "action": "add"})
        assert mapper.extract_media_path(row) is None

    def test_returns_none_when_column_missing(self):
        mapper = ContentMapper(["id", "action"])
        row = make_row(id="", action="add")
        assert mapper.extract_media_path(row) is None
