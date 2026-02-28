"""
Tests for src/mappers/response_mapper.py
"""

import pytest

from src.mappers.response_mapper import ResponseMapper


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def full_post():
    return {
        "id": 42,
        "title": {"rendered": "Hello World"},
        "content": {"rendered": "<p>Body text</p>"},
        "excerpt": {"rendered": "<p>Short excerpt</p>"},
        "slug": "hello-world",
        "status": "publish",
        "type": "post",
        "link": "https://example.com/hello-world",
        "date": "2024-01-15T10:00:00",
        "modified": "2024-01-16T12:00:00",
        "categories": [3, 5],
        "tags": [10],
        "featured_media": 99,
        "parent": 0,
        "image_url": "https://example.com/img.jpg",
        "acf": {"price": 100, "color": "red"},
    }


@pytest.fixture
def taxonomy_cache_full():
    return {
        "categories": {3: "News", 5: "Tutorials"},
        "tags": {10: "python"},
    }


# ── ResponseMapper.map_post_to_excel() ───────────────────────────────

class TestMapPostToExcel:
    def test_basic_fields(self, full_post):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        assert row["id"] == 42
        assert row["title"] == "Hello World"
        assert row["slug"] == "hello-world"
        assert row["status"] == "publish"
        assert row["link"] == "https://example.com/hello-world"

    def test_rendered_fields(self, full_post):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        assert row["content"] == "<p>Body text</p>"

    def test_excerpt_stripped_of_html(self, full_post):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        # Excerpt должен быть без HTML-тегов
        assert "<p>" not in row["excerpt"]
        assert "Short excerpt" in row["excerpt"]

    def test_action_is_skip(self, full_post):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        assert row["action"] == "skip"

    def test_taxonomy_ids_without_cache(self, full_post):
        """Без кэша IDs конвертируются в строку."""
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        # Без кэша categories/tags будут пустыми строками или строками IDs
        assert "categories" in row

    def test_taxonomy_ids_with_cache(self, full_post, taxonomy_cache_full):
        mapper = ResponseMapper(taxonomy_cache=taxonomy_cache_full)
        row = mapper.map_post_to_excel(full_post)
        assert "News" in row["categories"]
        assert "Tutorials" in row["categories"]
        assert "python" in row["tags"]

    def test_acf_fields_mapped(self, full_post):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        assert row["acf_price"] == 100
        assert row["acf_color"] == "red"

    def test_image_url_included(self, full_post):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel(full_post)
        assert row["image_url"] == "https://example.com/img.jpg"

    def test_string_title_passthrough(self):
        mapper = ResponseMapper()
        post = {"id": 1, "title": "Plain title", "featured_media": 0}
        row = mapper.map_post_to_excel(post)
        assert row["title"] == "Plain title"

    def test_empty_post_does_not_crash(self):
        mapper = ResponseMapper()
        row = mapper.map_post_to_excel({})
        assert row["id"] == ""
        assert row["action"] == "skip"


# ── ResponseMapper.map_term_to_excel() ───────────────────────────────

class TestMapTermToExcel:
    def test_basic_term_fields(self):
        term = {
            "id": 7,
            "name": "Technology",
            "slug": "technology",
            "description": "Tech category",
            "link": "https://example.com/tech",
        }
        row = ResponseMapper.map_term_to_excel(term)
        assert row["id"] == 7
        assert row["name"] == "Technology"
        assert row["slug"] == "technology"
        assert row["description"] == "Tech category"

    def test_action_is_skip(self):
        row = ResponseMapper.map_term_to_excel({"id": 1, "name": "Test"})
        assert row["action"] == "skip"

    def test_acf_fields_on_term(self):
        term = {"id": 5, "name": "Tech", "acf": {"icon": "tech-icon"}}
        row = ResponseMapper.map_term_to_excel(term)
        assert row["acf_icon"] == "tech-icon"

    def test_skip_fields_excluded(self):
        term = {"id": 1, "name": "T", "_links": {"self": []}, "taxonomy": "category", "count": 5}
        row = ResponseMapper.map_term_to_excel(term)
        assert "_links" not in row
        assert "taxonomy" not in row
        assert "count" not in row
