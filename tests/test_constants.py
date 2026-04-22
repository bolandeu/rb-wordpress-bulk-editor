"""
Tests for src/constants.py helper functions.
"""

import pytest
from pathlib import Path

from src.constants import (
    build_nested_payload,
    is_dot_notation_column,
    is_taxonomy_column,
    read_nested_value,
    TAXONOMY_DELIMITER,
)
from src.utils.wp_helpers import wp_rendered, convert_taxonomy_ids_to_names
from src.utils.path_utils import resolve_media_file


# ── is_taxonomy_column ──────────────────────────────────────────────

class TestIsTaxonomyColumn:
    @pytest.mark.parametrize("col", ["categories", "tags", "category", "tag"])
    def test_standard_taxonomies(self, col):
        assert is_taxonomy_column(col) is True

    @pytest.mark.parametrize("col", ["Categories", "Tags"])
    def test_standard_taxonomies_case_insensitive(self, col):
        assert is_taxonomy_column(col) is True

    @pytest.mark.parametrize("col", ["genre-cat", "brand-cat", "color-cat"])
    def test_suffix_cat(self, col):
        assert is_taxonomy_column(col) is True

    @pytest.mark.parametrize("col", [
        "title", "content", "slug", "status", "acf_price",
        "id", "action", "catalog",  # 'catalog' ends with 'cat' but not '-cat'
    ])
    def test_negative_cases(self, col):
        assert is_taxonomy_column(col) is False

    def test_override_recognizes_custom_slug(self):
        assert is_taxonomy_column("brand", known_taxonomies=["brand", "regions"])

    def test_override_case_insensitive(self):
        assert is_taxonomy_column("Brand", known_taxonomies=["brand"])

    def test_override_preserves_standard_and_suffix(self):
        assert is_taxonomy_column("categories", known_taxonomies=["brand"])
        assert is_taxonomy_column("x-cat", known_taxonomies=["brand"])

    def test_no_override_brand_is_not_taxonomy(self):
        assert is_taxonomy_column("brand") is False


# ── is_dot_notation_column ──────────────────────────────────────────

class TestIsDotNotationColumn:
    @pytest.mark.parametrize("col", ["seo.title", "seo.og.image", "a.b.c.d"])
    def test_positive(self, col):
        assert is_dot_notation_column(col) is True

    @pytest.mark.parametrize("col", ["title", "seo_title", "acf_foo"])
    def test_no_dot(self, col):
        assert is_dot_notation_column(col) is False

    @pytest.mark.parametrize("col", ["seo..title", ".leading", "trailing.", "."])
    def test_empty_segment_invalid(self, col):
        assert is_dot_notation_column(col) is False


# ── build_nested_payload ────────────────────────────────────────────

class TestBuildNestedPayload:
    def test_flat_key(self):
        assert build_nested_payload("title", "X", {}) == {"title": "X"}

    def test_single_nesting(self):
        assert build_nested_payload("seo.title", "X", {}) == {"seo": {"title": "X"}}

    def test_deep_nesting(self):
        assert build_nested_payload("seo.og.image", "img.jpg", {}) == {
            "seo": {"og": {"image": "img.jpg"}}
        }

    def test_merges_into_existing(self):
        p = {"seo": {"title": "T"}}
        build_nested_payload("seo.description", "D", p)
        assert p == {"seo": {"title": "T", "description": "D"}}

    def test_overwrites_non_dict_segment(self):
        p = {"seo": "string"}
        build_nested_payload("seo.title", "X", p)
        assert p == {"seo": {"title": "X"}}

    def test_leaf_last_wins(self):
        p = {"seo": {"title": "old"}}
        build_nested_payload("seo.title", "new", p)
        assert p["seo"]["title"] == "new"

    def test_empty_path_noop(self):
        p = {"k": "v"}
        build_nested_payload("", "X", p)
        assert p == {"k": "v"}


# ── read_nested_value ───────────────────────────────────────────────

class TestReadNestedValue:
    def test_flat_key(self):
        assert read_nested_value("title", {"title": "T"}) == "T"

    def test_deep_path(self):
        assert read_nested_value("seo.og.image", {"seo": {"og": {"image": "img.jpg"}}}) == "img.jpg"

    def test_missing_returns_none(self):
        assert read_nested_value("seo.missing", {"seo": {}}) is None
        assert read_nested_value("missing", {}) is None

    def test_non_dict_midpath_returns_none(self):
        assert read_nested_value("seo.title", {"seo": "str"}) is None

    def test_bool_value_preserved(self):
        assert read_nested_value("seo.robots.index", {"seo": {"robots": {"index": False}}}) is False


# ── wp_rendered ─────────────────────────────────────────────────────

class TestWpRendered:
    def test_dict_with_rendered(self):
        assert wp_rendered({"rendered": "<p>Hello</p>"}) == "<p>Hello</p>"

    def test_dict_without_rendered(self):
        assert wp_rendered({"raw": "Hello"}) == ""

    def test_dict_without_rendered_custom_default(self):
        assert wp_rendered({"raw": "Hello"}, default="N/A") == "N/A"

    def test_string_value(self):
        assert wp_rendered("plain text") == "plain text"

    def test_none_value(self):
        assert wp_rendered(None) == ""

    def test_none_custom_default(self):
        assert wp_rendered(None, default="fallback") == "fallback"

    def test_empty_string(self):
        assert wp_rendered("") == ""

    def test_numeric_value(self):
        assert wp_rendered(42) == "42"


# ── convert_taxonomy_ids_to_names ───────────────────────────────────

class TestConvertTaxonomyIdsToNames:
    def test_normal_conversion(self, taxonomy_cache):
        result = convert_taxonomy_ids_to_names([3, 5], taxonomy_cache, "categories")
        assert result == "News; Tutorials"

    def test_empty_list(self, taxonomy_cache):
        assert convert_taxonomy_ids_to_names([], taxonomy_cache, "categories") == ""

    def test_missing_cache(self):
        result = convert_taxonomy_ids_to_names([3, 5], {}, "categories")
        assert result == f"3{TAXONOMY_DELIMITER}5"

    def test_none_cache(self):
        result = convert_taxonomy_ids_to_names([3, 5], None, "categories")
        assert result == f"3{TAXONOMY_DELIMITER}5"

    def test_missing_ids_in_cache(self, taxonomy_cache):
        result = convert_taxonomy_ids_to_names([3, 999], taxonomy_cache, "categories")
        assert result == "News; 999"

    def test_single_id(self, taxonomy_cache):
        assert convert_taxonomy_ids_to_names([10], taxonomy_cache, "tags") == "python"


# ── resolve_media_file ──────────────────────────────────────────────

class TestResolveMediaFile:
    def test_absolute_path(self, tmp_path):
        abs_path = str(tmp_path / "photo.jpg")
        result = resolve_media_file(abs_path, tmp_path / "media")
        assert result == Path(abs_path)
        assert result.is_absolute()

    def test_cwd_relative_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "image.png").write_bytes(b"PNG")
        result = resolve_media_file("image.png", tmp_path / "other")
        assert result == Path("image.png")

    def test_media_folder_fallback(self, tmp_media_folder, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = resolve_media_file("photo.jpg", tmp_media_folder)
        assert result == tmp_media_folder / "photo.jpg"
        assert result.exists()

    def test_not_found_returns_relative(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = resolve_media_file("missing.jpg", tmp_path / "empty")
        assert result == Path("missing.jpg")
        assert not result.exists()
