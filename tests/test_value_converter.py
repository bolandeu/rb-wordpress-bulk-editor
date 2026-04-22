"""
Тесты для parse_bool_default_true — парсер bool-полей с дефолтом True.
"""

import pandas as pd
import pytest

from src.mappers.value_converter import parse_bool_default_true


class TestParseBoolDefaultTrue:
    @pytest.mark.parametrize("value", [None, pd.NA, float("nan"), "", "   "])
    def test_empty_defaults_to_true(self, value):
        assert parse_bool_default_true(value) is True

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_passthrough(self, value):
        assert parse_bool_default_true(value) is value

    def test_zero_is_false(self):
        assert parse_bool_default_true(0) is False
        assert parse_bool_default_true(0.0) is False

    @pytest.mark.parametrize("value", [1, 2, -1, 0.5, 42])
    def test_nonzero_is_true(self, value):
        assert parse_bool_default_true(value) is True

    @pytest.mark.parametrize("value", ["0", "нет", "Нет", "НЕТ", "no", "NO", "false", "False", "FALSE", "f", "n"])
    def test_falsy_strings(self, value):
        assert parse_bool_default_true(value) is False

    @pytest.mark.parametrize("value", ["1", "да", "yes", "true", "True"])
    def test_truthy_strings(self, value):
        assert parse_bool_default_true(value) is True

    @pytest.mark.parametrize("value", ["foo", "???", "maybe", "2025-01-01"])
    def test_unknown_strings_default_true(self, value):
        """Непонятные строки → True (консервативно)."""
        assert parse_bool_default_true(value) is True

    def test_whitespace_stripped(self):
        assert parse_bool_default_true("  нет  ") is False
        assert parse_bool_default_true("\ttrue\n") is True
