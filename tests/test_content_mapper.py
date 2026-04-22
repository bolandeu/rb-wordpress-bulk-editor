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


# ── Dot-notation (вложенный payload) ─────────────────────────────────

class TestDotNotationPayload:
    def test_single_nesting(self):
        mapper = ContentMapper(["seo.title", "id", "action"])
        payload = mapper.map_row(make_row(**{"seo.title": "X", "id": "", "action": "add"}))
        assert payload["seo"] == {"title": "X"}

    def test_multiple_nested_merged(self):
        mapper = ContentMapper(["seo.title", "seo.description", "id", "action"])
        payload = mapper.map_row(make_row(**{
            "seo.title": "T", "seo.description": "D", "id": "", "action": "add"
        }))
        assert payload["seo"] == {"title": "T", "description": "D"}

    def test_deep_nesting(self):
        mapper = ContentMapper(["seo.og.image", "id", "action"])
        payload = mapper.map_row(make_row(**{
            "seo.og.image": "img.jpg", "id": "", "action": "add"
        }))
        assert payload == {"seo": {"og": {"image": "img.jpg"}}}

    def test_empty_dot_value_skipped(self):
        mapper = ContentMapper(["seo.title", "id", "action"])
        payload = mapper.map_row(make_row(**{"seo.title": "", "id": "", "action": "add"}))
        assert "seo" not in payload

    def test_legacy_seo_flat_and_dot_coexist(self):
        """seo_title (flat) и seo.title (nested) живут в разных ключах payload."""
        mapper = ContentMapper(["seo_title", "seo.title", "id", "action"])
        payload = mapper.map_row(make_row(**{
            "seo_title": "flat", "seo.title": "nested", "id": "", "action": "add"
        }))
        assert payload["seo_title"] == "flat"
        assert payload["seo"]["title"] == "nested"

    def test_acf_legacy_and_dot_merge(self):
        mapper = ContentMapper(["acf_old", "acf.new", "id", "action"])
        payload = mapper.map_row(make_row(**{
            "acf_old": "O", "acf.new": "N", "id": "", "action": "add"
        }))
        assert payload["acf"] == {"old": "O", "new": "N"}

    def test_filter_by_dot_path(self):
        mapper = ContentMapper(
            ["seo.title", "seo.description", "id", "action"],
            allowed_columns=["seo.title"],
        )
        payload = mapper.map_row(make_row(**{
            "seo.title": "X", "seo.description": "Y", "id": "", "action": "update"
        }))
        assert payload == {"seo": {"title": "X"}}


# ── Bool-поля «по умолчанию true» ────────────────────────────────────

class TestBoolDefaultTrueFields:
    BOOL_COLS = ["seo.robots.index", "seo.robots.follow", "seo.sitemap.include"]

    def _cm(self):
        return ContentMapper(self.BOOL_COLS + ["id", "action"])

    def test_empty_defaults_to_true(self):
        payload = self._cm().map_row(make_row(**{
            "seo.robots.index": "",
            "seo.robots.follow": "",
            "seo.sitemap.include": "",
            "id": "", "action": "update",
        }))
        assert payload["seo"]["robots"] == {"index": True, "follow": True}
        assert payload["seo"]["sitemap"] == {"include": True}

    def test_falsy_values_parsed(self):
        payload = self._cm().map_row(make_row(**{
            "seo.robots.index": "нет",
            "seo.robots.follow": "false",
            "seo.sitemap.include": "0",
            "id": "", "action": "update",
        }))
        assert payload["seo"]["robots"] == {"index": False, "follow": False}
        assert payload["seo"]["sitemap"] == {"include": False}

    def test_column_absent_not_sent(self):
        """Отсутствие колонки = preserve-existing (не шлём)."""
        mapper = ContentMapper(["title", "id", "action"])
        payload = mapper.map_row(make_row(title="T", id="", action="add"))
        assert "robots" not in payload.get("seo", {})
        assert "sitemap" not in payload.get("seo", {})


# ── known_taxonomies override ────────────────────────────────────────

class TestKnownTaxonomies:
    def test_custom_taxonomy_excluded_from_content(self):
        """`brand` с override — таксономия, не попадает в content и payload как поле."""
        mapper = ContentMapper(["title", "brand", "id", "action"], known_taxonomies=["brand"])
        payload = mapper.map_row(make_row(title="T", brand="BMW", id="", action="add"))
        assert "brand" not in payload
        assert payload["title"] == "T"

    def test_custom_taxonomy_ids_injected(self):
        mapper = ContentMapper(["title", "brand", "id", "action"], known_taxonomies=["brand"])
        payload = mapper.map_row(
            make_row(title="T", brand="BMW", id="", action="add"),
            taxonomy_ids={"brand": [7]},
        )
        assert payload["brand"] == [7]
