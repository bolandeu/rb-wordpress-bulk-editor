"""
Tests for src/wordpress/discovery.py
"""

from src.wordpress.discovery import WordPressDiscovery


class FakeClient:
    async def get(self, endpoint, params=None, **kwargs):
        if endpoint == "/wp-json/wp/v2/types":
            return {
                "post": {"name": "Записи"},
                "rb_review": {"name": "Отзывы"},
                "wp_block": {"name": "Blocks"},
            }
        if endpoint == "/wp-json/wp/v2/taxonomies":
            return {
                "category": {"name": "Рубрики"},
                "nav_menu": {"name": "Menus"},
            }
        return {}


class TestWordPressDiscovery:
    async def test_discover_post_types_skips_patterns_by_default(self):
        discovery = WordPressDiscovery(
            FakeClient(),
            skip_post_type_patterns=["rb_", "wp_"],
        )

        assert await discovery.discover_post_types() == ["posts"]

    async def test_discover_post_types_can_include_skipped_for_explicit_sheets(self):
        discovery = WordPressDiscovery(
            FakeClient(),
            skip_post_type_patterns=["rb_", "wp_"],
        )

        assert await discovery.discover_post_types(include_skipped=True) == [
            "posts",
            "rb_review",
            "wp_block",
        ]
