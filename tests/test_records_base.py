"""
Tests for src/records/base.py — семантика «пусто/отсутствует» и группировка.

`is_missing`/`is_blank` заменили `pd.isna`/`pd.notna` во всём sync-слое,
поэтому проверяются на всех типах, которые реально приходят из Excel и JSON.
"""

import math

import pandas as pd
import pytest

from rb_wordpress_bulk_editor.records.base import (
    Record,
    collect_fields,
    group_by_discriminator,
    has_value,
    is_blank,
    is_missing,
)


# (значение, is_missing, is_blank)
VALUES = [
    (None, True, True),
    (float("nan"), True, True),
    (math.nan, True, True),
    (pd.NA, True, True),
    (pd.NaT, True, True),
    ("", False, True),
    (" ", False, False),
    ("текст", False, False),
    (0, False, False),
    (0.0, False, False),
    (False, False, False),
    (True, False, False),
    ([], False, False),
    ([1], False, False),
    ({}, False, False),
]


class TestIsMissing:
    @pytest.mark.parametrize("value,expected,_blank", VALUES)
    def test_matches_pandas_isna(self, value, expected, _blank):
        assert is_missing(value) is expected
        # Та же семантика, что у pd.isna() для скаляров
        if not isinstance(value, (list, dict)):
            assert is_missing(value) is bool(pd.isna(value))


class TestIsBlank:
    @pytest.mark.parametrize("value,_missing,expected", VALUES)
    def test_blank_values(self, value, _missing, expected):
        assert is_blank(value) is expected


class TestHasValue:
    def test_present_and_filled(self):
        assert has_value({"title": "X"}, "title") is True

    def test_empty_string(self):
        assert has_value({"title": ""}, "title") is False

    def test_missing_key(self):
        assert has_value({}, "title") is False


class TestCollectFields:
    def test_union_in_first_seen_order(self):
        records = [
            Record("posts", False, {"action": "add", "title": "A"}, 0),
            Record("posts", False, {"title": "B", "slug": "b"}, 1),
        ]
        assert collect_fields(records) == ["action", "title", "slug"]


class TestGroupByDiscriminator:
    def test_splits_content_and_taxonomy(self):
        records = [
            Record("posts", False, {}, 0),
            Record("posts", True, {}, 1),
        ]
        groups = group_by_discriminator(records)
        assert [(g[0].discriminator, g[0].is_taxonomy) for g in groups] == [
            ("posts", False),
            ("posts", True),
        ]
