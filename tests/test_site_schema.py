"""
Tests for src/wordpress/site_schema.py — таксономии и типы записей сайта.

Сайт — источник правды: список таксономий и их привязка к типам берутся из
REST API при каждом прогоне, а не из списка в .env.
"""

import pytest

from rb_wordpress_bulk_editor.exceptions import NetworkError
from rb_wordpress_bulk_editor.wordpress.site_schema import SiteSchema, load_site_schema

TAXONOMIES = {
    "category": {"slug": "category", "rest_base": "categories", "types": ["post"]},
    "post_tag": {"slug": "post_tag", "rest_base": "tags", "types": ["post"]},
    "rb_topic": {"slug": "rb_topic", "rest_base": "rb_topic", "types": ["rb_services", "rb_review"]},
    "rb_services_cat": {"slug": "rb_services_cat", "rest_base": "rb_services_cat", "types": ["rb_services"]},
}
TYPES = {
    "post": {"slug": "post", "rest_base": "posts"},
    "page": {"slug": "page", "rest_base": "pages"},
    "rb_services": {"slug": "rb_services", "rest_base": "rb_services"},
}


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    async def get(self, endpoint, params=None, **kwargs):
        self.requests.append(endpoint)
        if self.fail:
            raise NetworkError("сайт недоступен")
        return {"/wp-json/wp/v2/taxonomies": TAXONOMIES, "/wp-json/wp/v2/types": TYPES}[endpoint]


@pytest.fixture
async def schema():
    return await load_site_schema(FakeClient())


class TestSiteSchema:
    async def test_taxonomy_by_slug_or_rest_base(self, schema):
        assert schema.taxonomy("categories") == "category"
        assert schema.taxonomy("category") == "category"
        assert schema.taxonomy("tags") == "post_tag"
        assert schema.taxonomy("rb_topic") == "rb_topic"
        assert schema.taxonomy("rb_servces_cat") is None

    async def test_type_by_slug_or_rest_base(self, schema):
        assert schema.has_type("posts") and schema.has_type("post")
        assert schema.has_type("rb_services")
        assert not schema.has_type("rb_servces")

    async def test_attachment_to_type(self, schema):
        assert schema.is_attached("rb_services", "rb_topic")
        assert schema.is_attached("posts", "categories")
        assert not schema.is_attached("pages", "categories")
        assert not schema.is_attached("posts", "rb_services_cat")

    async def test_taxonomy_names_include_both_forms(self, schema):
        assert {"categories", "category", "tags", "post_tag", "rb_topic"} <= schema.taxonomy_names()

    async def test_suggestions(self, schema):
        assert schema.closest_taxonomy("rb_servces_cat") == "rb_services_cat"
        assert schema.closest_type("rb_servces") == "rb_services"

    async def test_failure_is_raised_not_replaced_with_defaults(self):
        """Молча подставить categories/tags нельзя: проверка сочла бы rb_topic обычным полем."""
        with pytest.raises(NetworkError):
            await load_site_schema(FakeClient(fail=True))

    def test_empty_schema(self):
        schema = SiteSchema.from_rest({}, {})
        assert schema.taxonomy("categories") is None
