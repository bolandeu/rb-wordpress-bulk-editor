"""
Tests for src/records/preflight.py — проверка набора данных перед отправкой.

Проверка работает на уровне `Record`, поэтому одинакова для Excel и JSON;
здесь записи собираются руками, без файлов.
"""

import pytest

from src.records.base import Record
from src.records.preflight import FieldRules, check_records


def post(write_key=2, discriminator="posts", **data):
    return Record(discriminator=discriminator, is_taxonomy=False, data=data, write_key=write_key)


def term(write_key=2, discriminator="category", **data):
    return Record(discriminator=discriminator, is_taxonomy=True, data=data, write_key=write_key)


def check(*records, rules=None, base_dir=None):
    return check_records("site.ru", list(records), rules or FieldRules(), base_dir=base_dir)


def test_clean_records_pass():
    result = check(
        post(2, action="add", title="A", slug="a", status="publish", content="<p>x</p>",
             **{"seo.title": "T", "seo.robots.index": "да", "rb_services_cat": "Перевозки",
                "acf.anything_goes": "1", "custom_permalink": "a/"}),
        term(3, action="add", name="Перевозки", slug="perevozki", description=""),
    )

    assert result.errors == []
    assert result.warnings == []
    assert result.records == 2


# ── Имена полей ──────────────────────────────────────────────────────

class TestFieldNames:
    def test_typo_in_seo_field_is_error_with_suggestion(self):
        result = check(post(action="update", id=1, **{"seo.descripton": "d"}))

        assert len(result.errors) == 1
        assert "seo.descripton" in result.errors[0]
        assert "seo.description" in result.errors[0]

    def test_typo_in_plain_field_suggests_known_name(self):
        result = check(post(action="add", tilte="A"))

        assert "tilte" in result.errors[0] and "'title'" in result.errors[0]

    def test_wrong_case_suggests_lowercase(self):
        result = check(post(action="add", Title="A"))

        assert "'title'" in result.errors[0]

    def test_unknown_field_reported_once_per_dataset(self):
        result = check(*[post(n, action="update", id=n, tilte="A") for n in range(2, 12)])

        assert len(result.errors) == 1
        assert "10 записей" in result.errors[0]

    @pytest.mark.parametrize("name", ["acf.whatever.deep", "acf_legacy_field", "meta._yoast_wpseo_title"])
    def test_acf_and_meta_fields_are_not_checked(self, name):
        result = check(post(action="add", title="A", **{name: "x"}))

        assert result.errors == []

    def test_unknown_field_only_on_skip_records_is_warning(self):
        result = check(post(action="skip", id=1, tilte="A"), post(3, action="", tilte="B"))

        assert result.errors == []
        assert len(result.warnings) == 1 and "tilte" in result.warnings[0]

    def test_seo_fields_from_settings_are_known(self):
        rules = FieldRules(seo_fields=["seo.custom.thing"])

        assert check(post(action="add", **{"seo.custom.thing": "x"}), rules=rules).errors == []

    def test_extra_fields_from_settings_are_known(self):
        rules = FieldRules(extra_fields=["my_field"])

        assert check(post(action="add", my_field="x"), rules=rules).errors == []

    def test_unknown_seo_field_without_settings_is_error(self):
        assert check(post(action="add", **{"seo.custom.thing": "x"})).errors


# ── Имена таксономий ─────────────────────────────────────────────────

class TestTaxonomyNames:
    @pytest.mark.parametrize("name", [
        "categories", "tags", "rb_services_cat", "genre-tax", "region_term", "cat_services", "post_tag",
    ])
    def test_standard_names_pass(self, name):
        assert check(post(action="add", **{name: "X"})).errors == []

    @pytest.mark.parametrize("name", ["RB_Services_Cat", "Genre-Tax"])
    def test_uppercase_taxonomy_name_is_error(self, name):
        result = check(post(action="add", **{name: "X"}))

        assert len(result.errors) == 1
        assert name.lower() in result.errors[0]

    @pytest.mark.parametrize("name", ["servicescat", "services cat", "services.cat"])
    def test_name_without_separator_is_unknown_field(self, name):
        assert check(post(action="add", **{name: "X"})).errors

    def test_custom_taxonomy_from_settings(self):
        rules = FieldRules(known_taxonomies=["brand"])

        assert check(post(action="add", brand="X"), rules=rules).errors == []

    def test_term_record_taxonomy_name_is_checked(self):
        result = check(term(action="add", discriminator="Services_Cat", name="X"))

        assert len(result.errors) == 1 and "services_cat" in result.errors[0]


# ── Значения ─────────────────────────────────────────────────────────

class TestValues:
    @pytest.mark.parametrize("action", ["update", "delete"])
    def test_update_and_delete_need_id(self, action):
        result = check(post(action=action, id="", title="A"))

        assert len(result.errors) == 1 and "id" in result.errors[0]

    def test_action_alias_is_normalized(self):
        result = check(post(action="u", title="A"))

        assert len(result.errors) == 1 and "id" in result.errors[0]

    def test_add_with_id_is_warning(self):
        result = check(post(action="add", id=5, title="A"))

        assert result.errors == []
        assert len(result.warnings) == 1 and "id" in result.warnings[0]

    def test_unknown_status_is_error(self):
        result = check(post(action="add", title="A", status="published"))

        assert len(result.errors) == 1 and "published" in result.errors[0]

    def test_duplicate_slug_within_type_is_error(self):
        result = check(
            post(2, action="add", title="A", slug="same"),
            post(3, action="add", title="B", slug="same"),
            post(4, discriminator="pages", action="add", title="C", slug="same"),
        )

        assert len(result.errors) == 1
        assert "same" in result.errors[0] and "2" in result.errors[0] and "3" in result.errors[0]

    def test_semicolon_without_space_in_taxonomy_is_error(self):
        result = check(post(action="add", title="A", rb_services_cat="Перевозки;Склад"))

        assert len(result.errors) == 1 and "; " in result.errors[0]

    def test_comma_in_taxonomy_is_warning(self):
        result = check(post(action="add", title="A", rb_services_cat="Перевозки, Склад"))

        assert result.errors == []
        assert len(result.warnings) == 1

    @pytest.mark.parametrize("value", ["", "да", "нет", "0", "1", "true", "False", True, 0])
    def test_bool_field_accepted_values(self, value):
        assert check(post(action="add", title="A", **{"seo.robots.index": value})).errors == []

    def test_bool_field_garbage_is_error(self):
        result = check(post(action="add", title="A", **{"seo.robots.index": "noindex"}))

        assert len(result.errors) == 1 and "noindex" in result.errors[0]

    def test_values_of_skip_records_are_not_checked(self):
        result = check(post(action="skip", status="whatever", **{"seo.robots.index": "??"}))

        assert result.errors == [] and result.warnings == []

    def test_unknown_action_is_warning(self):
        result = check(post(action="publish", id=1))

        assert result.errors == []
        assert "неизвестный action" in result.warnings[0]


# ── Значения-пути к файлам ───────────────────────────────────────────

class TestFileValues:
    def test_missing_file_on_sending_record_is_error(self, tmp_path):
        result = check(post(action="add", title="A", **{"acf.more": "content/1.html"}), base_dir=tmp_path)

        assert len(result.errors) == 1 and "1.html" in result.errors[0]

    def test_missing_file_on_skip_record_is_warning(self, tmp_path):
        result = check(post(action="skip", **{"acf.more": "content/1.html"}), base_dir=tmp_path)

        assert result.errors == []
        assert len(result.warnings) == 1

    def test_without_base_dir_files_are_not_checked(self):
        assert check(post(action="add", title="A", **{"acf.more": "content/1.html"})).errors == []


def test_location_uses_custom_label():
    result = check_records(
        "posts", [post(0, action="update", title="A")], FieldRules(),
        where=lambda record: f"строка {record.write_key + 2}",
    )

    assert "строка 2" in result.errors[0]


# ── Сверка с сайтом ──────────────────────────────────────────────────

from src.wordpress.site_schema import SiteSchema  # noqa: E402

SITE = SiteSchema.from_rest(
    {
        "category": {"slug": "category", "rest_base": "categories", "types": ["post"]},
        "rb_topic": {"slug": "rb_topic", "rest_base": "rb_topic", "types": ["rb_services"]},
        "rb_services_cat": {
            "slug": "rb_services_cat", "rest_base": "rb_services_cat", "types": ["rb_services"],
        },
    },
    {
        "post": {"slug": "post", "rest_base": "posts"},
        "rb_services": {"slug": "rb_services", "rest_base": "rb_services"},
    },
)
ONLINE = FieldRules(site=SITE)


def service(write_key=2, **data):
    return post(write_key, discriminator="rb_services", **data)


class TestSiteChecks:
    def test_site_taxonomy_without_convention_is_known(self):
        """rb_topic не подходит под cat/tax/term/tag, но на сайте есть — WP_TAXONOMIES не нужен."""
        assert check(service(action="add", title="A", rb_topic="Логистика"), rules=ONLINE).errors == []

    def test_same_column_without_site_is_unknown_field(self):
        assert check(service(action="add", title="A", rb_topic="Логистика")).errors

    def test_taxonomy_missing_on_site_is_error_with_suggestion(self):
        result = check(service(action="add", title="A", rb_servces_cat="X"), rules=ONLINE)

        assert len(result.errors) == 1
        assert "на сайте нет" in result.errors[0] and "rb_services_cat" in result.errors[0]

    def test_taxonomy_not_attached_to_type_is_error(self):
        result = check(service(action="add", title="A", categories="Новости"), rules=ONLINE)

        assert len(result.errors) == 1
        assert "не привязана" in result.errors[0] and "rb_services" in result.errors[0]

    def test_blank_value_of_foreign_taxonomy_is_fine(self):
        """В общем табе у записи чужого типа колонка есть, но пустая — на сайт не уходит."""
        assert check(service(action="add", title="A", categories=""), rules=ONLINE).errors == []

    def test_attachment_reported_once_per_type(self):
        records = [service(n, action="add", title=f"A{n}", categories="X") for n in range(2, 6)]
        result = check(*records, rules=ONLINE)

        assert len(result.errors) == 1 and "всего 4" in result.errors[0]

    def test_term_record_taxonomy_missing_on_site(self):
        result = check(term(action="add", discriminator="rb_servces_cat", name="X"), rules=ONLINE)

        assert len(result.errors) == 1 and "rb_services_cat" in result.errors[0]

    def test_term_record_taxonomy_by_rest_name(self):
        assert check(term(action="add", discriminator="categories", name="X"), rules=ONLINE).errors == []

    def test_content_type_missing_on_site(self):
        result = check(post(discriminator="rb_servces", action="add", title="A"), rules=ONLINE)

        assert len(result.errors) == 1
        assert "rb_servces" in result.errors[0] and "rb_services" in result.errors[0]

    def test_problems_on_skip_records_are_warnings(self):
        result = check(service(action="skip", id=1, rb_servces_cat="X"), rules=ONLINE)

        assert result.errors == []
        assert result.warnings


class TestOfflineTaxonomiesFromFile:
    def test_term_records_declare_taxonomy_for_columns(self):
        """Без сайта: раз в файле есть термины rb_topic, колонка rb_topic — таксономия."""
        result = check(
            term(2, action="skip", discriminator="rb_topic", name="Логистика"),
            service(3, action="add", title="A", rb_topic="Логистика"),
        )

        assert result.errors == []
