"""
Tests for src/records/filters.py — предикат `--filter field=value`.
"""

import pytest

from src.exceptions import ValidationError
from src.records.base import Record
from src.records.filters import matches, parse_filters


def content(data, discriminator="rb_services") -> Record:
    return Record(discriminator=discriminator, is_taxonomy=False, data=data, write_key=2)


def term(data, discriminator="rb_services_cat") -> Record:
    return Record(discriminator=discriminator, is_taxonomy=True, data=data, write_key=3)


class TestParseFilters:
    def test_empty(self):
        assert parse_filters(None) == []
        assert parse_filters([]) == []

    def test_pairs_and_trimming(self):
        assert parse_filters([" status = publish ", "acf.f1=да"]) == [
            ("status", "publish"),
            ("acf.f1", "да"),
        ]

    def test_value_may_contain_equals(self):
        assert parse_filters(["slug=a=b"]) == [("slug", "a=b")]

    def test_empty_value_allowed(self):
        """Пустое значение — валидный фильтр «поле не заполнено»."""
        assert parse_filters(["id="]) == [("id", "")]

    @pytest.mark.parametrize("raw", ["status", "=publish", "  =x"])
    def test_bad_format(self, raw):
        with pytest.raises(ValidationError) as exc:
            parse_filters([raw])
        assert exc.value.exit_code == 2
        assert "field=value" in exc.value.message


class TestMatches:
    def test_no_filters_passes_everything(self):
        assert matches(content({"status": "draft"}), [])

    def test_exact_match(self):
        record = content({"status": "publish"})
        assert matches(record, [("status", "publish")])
        assert not matches(record, [("status", "draft")])

    def test_conditions_are_anded(self):
        record = content({"status": "publish", "action": "add"})
        assert matches(record, [("status", "publish"), ("action", "add")])
        assert not matches(record, [("status", "publish"), ("action", "update")])

    def test_dot_notation_is_a_plain_key(self):
        record = content({"acf.f1": "да", "seo.title": "Заголовок"})
        assert matches(record, [("acf.f1", "да")])
        assert not matches(record, [("acf.f1", "нет")])

    def test_missing_field_matches_empty_value_only(self):
        record = content({"status": "publish"})
        assert not matches(record, [("slug", "x")])
        assert matches(record, [("slug", "")])

    def test_non_string_values_compared_as_strings(self):
        assert matches(content({"id": 42}), [("id", "42")])

    def test_type_matches_discriminator_of_content_record(self):
        """`type` вырезан из data источником — фильтр берёт его из дискриминатора."""
        record = content({"title": "Услуга"})
        assert matches(record, [("type", "rb_services")])
        assert not matches(record, [("type", "page")])

    def test_taxonomy_matches_discriminator_of_term_record(self):
        record = term({"name": "Перевозки"})
        assert matches(record, [("taxonomy", "rb_services_cat")])
        assert not matches(record, [("type", "rb_services_cat")])
