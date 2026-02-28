"""
Tests for src/cli/common.py — parse_comma_list.
"""

from src.cli.common import parse_comma_list


class TestParseCommaList:
    def test_normal(self):
        assert parse_comma_list("a,b,c") == ["a", "b", "c"]

    def test_with_spaces(self):
        assert parse_comma_list("  title , slug , status  ") == ["title", "slug", "status"]

    def test_empty_string(self):
        assert parse_comma_list("") is None

    def test_none(self):
        assert parse_comma_list(None) is None

    def test_single_value(self):
        assert parse_comma_list("title") == ["title"]

    def test_trailing_comma(self):
        result = parse_comma_list("a,b,")
        assert result == ["a", "b"]

    def test_only_commas(self):
        assert parse_comma_list(",,,") == []
