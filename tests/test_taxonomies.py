"""
Tests for src/wordpress/taxonomies.py — разбор строк таксономий, иерархия,
primary-термины.
"""

import pytest

from src.exceptions import ValidationError
from src.wordpress.taxonomies import (
    TaxonomiesEndpoint,
    TaxonomyCache,
    normalize_taxonomy_name,
    parse_term_specs,
    primary_term_meta_key,
)


class FakeTermsClient:
    """Минимальный фейковый WP-клиент с in-memory хранилищем терминов."""

    def __init__(self, terms=None):
        self.terms = list(terms or [])
        self.next_id = max([t["id"] for t in self.terms], default=0) + 1
        self.created = []
        self.queries = []

    async def get(self, endpoint, params=None, **kwargs):
        params = params or {}
        self.queries.append(params)
        result = self.terms
        if "search" in params:
            needle = params["search"].lower()
            result = [t for t in result if needle in t["name"].lower()]
        if "parent" in params:
            parent = int(params["parent"])
            result = [t for t in result if int(t.get("parent", 0) or 0) == parent]
        return list(result)

    async def post(self, endpoint, data=None, **kwargs):
        data = data or {}
        term = {
            "id": self.next_id,
            "name": data["name"],
            "parent": int(data.get("parent", 0) or 0),
        }
        self.next_id += 1
        self.terms.append(term)
        self.created.append(data)
        return term


# ── parse_term_specs ────────────────────────────────────────────────


class TestParseTermSpecs:
    def test_empty(self):
        assert parse_term_specs("") == []
        assert parse_term_specs("   ") == []

    def test_single_term(self):
        assert parse_term_specs("Портьеры") == [["Портьеры"]]

    def test_flat_terms(self):
        assert parse_term_specs("Портьеры; Шторы") == [["Портьеры"], ["Шторы"]]

    def test_hierarchy_only(self):
        assert parse_term_specs("Термин2>Термин2.1") == [["Термин2", "Термин2.1"]]

    def test_deep_hierarchy(self):
        assert parse_term_specs("a>b>c") == [["a", "b", "c"]]

    def test_mixed(self):
        assert parse_term_specs("термин1; термин2>термин2.1") == [
            ["термин1"],
            ["термин2", "термин2.1"],
        ]

    def test_whitespace_around_delimiters(self):
        assert parse_term_specs("  a  >  b  ;  c ") == [["a", "b"], ["c"]]

    def test_trailing_delimiter_ignored(self):
        assert parse_term_specs("a; ") == [["a"]]

    @pytest.mark.parametrize("value", ["a>", ">b", "a>>b", "a; b>"])
    def test_malformed_hierarchy_raises(self, value):
        with pytest.raises(ValidationError):
            parse_term_specs(value)


# ── primary_term_meta_key ───────────────────────────────────────────


class TestPrimaryTermMetaKey:
    @pytest.mark.parametrize("taxonomy", ["categories", "category", "Categories"])
    def test_category(self, taxonomy):
        assert primary_term_meta_key(taxonomy) == "_yoast_wpseo_primary_category"

    @pytest.mark.parametrize("taxonomy", ["tags", "tag"])
    def test_tags(self, taxonomy):
        assert primary_term_meta_key(taxonomy) == "_yoast_wpseo_primary_post_tag"

    def test_custom_taxonomy(self):
        assert primary_term_meta_key("rb_services_cat") == "_yoast_wpseo_primary_rb_services_cat"


# ── process_taxonomy_string ─────────────────────────────────────────


class TestProcessTaxonomyString:
    async def test_empty_string(self):
        endpoint = TaxonomiesEndpoint(FakeTermsClient(), "categories")
        assert await endpoint.process_taxonomy_string("") == ([], None)

    async def test_single_existing_term(self):
        client = FakeTermsClient([{"id": 5, "name": "Портьеры", "parent": 0}])
        endpoint = TaxonomiesEndpoint(client, "categories")

        term_ids, primary = await endpoint.process_taxonomy_string("Портьеры")

        assert term_ids == [5]
        assert primary == 5
        assert client.created == []
        # Плоский поиск не сужается по родителю — поведение как до иерархии
        assert all("parent" not in q for q in client.queries)

    async def test_flat_terms_create_and_primary_is_first(self):
        client = FakeTermsClient()
        endpoint = TaxonomiesEndpoint(client, "categories")

        term_ids, primary = await endpoint.process_taxonomy_string("Портьеры; Шторы")

        assert len(term_ids) == 2
        assert primary == term_ids[0]
        assert [c["name"] for c in client.created] == ["Портьеры", "Шторы"]
        assert all("parent" not in c for c in client.created)

    async def test_hierarchy_creates_ancestors_in_order(self):
        client = FakeTermsClient()
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        term_ids, primary = await endpoint.process_taxonomy_string("Родитель>Ребёнок")

        assert [c["name"] for c in client.created] == ["Родитель", "Ребёнок"]
        parent_id = client.terms[0]["id"]
        child_id = client.terms[1]["id"]
        assert client.created[0]["parent"] == 0
        assert client.created[1]["parent"] == parent_id
        # Присваивается только последний термин цепочки
        assert term_ids == [child_id]
        assert primary == child_id

    async def test_hierarchy_reuses_existing_ancestor(self):
        client = FakeTermsClient([{"id": 7, "name": "Родитель", "parent": 0}])
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        term_ids, _ = await endpoint.process_taxonomy_string("Родитель>Ребёнок")

        assert [c["name"] for c in client.created] == ["Ребёнок"]
        assert client.created[0]["parent"] == 7
        assert term_ids == [client.terms[-1]["id"]]

    async def test_same_name_under_different_parents_not_conflated(self):
        client = FakeTermsClient(
            [
                {"id": 1, "name": "A", "parent": 0},
                {"id": 2, "name": "B", "parent": 0},
                {"id": 3, "name": "Общий", "parent": 1},
            ]
        )
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        term_ids, _ = await endpoint.process_taxonomy_string("B>Общий")

        # Существующий "Общий" сидит под A — под B должен появиться новый
        assert [c["name"] for c in client.created] == ["Общий"]
        assert client.created[0]["parent"] == 2
        assert term_ids != [3]

    async def test_mixed_flat_and_hierarchy(self):
        client = FakeTermsClient()
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        term_ids, primary = await endpoint.process_taxonomy_string("термин1; термин2>термин2.1")

        assert [c["name"] for c in client.created] == ["термин1", "термин2", "термин2.1"]
        assert len(term_ids) == 2
        # primary — первая спецификация строки (плоский "термин1")
        assert primary == term_ids[0]
        assert primary == client.terms[0]["id"]

    async def test_failed_term_does_not_break_others(self):
        class FailingClient(FakeTermsClient):
            async def post(self, endpoint, data=None, **kwargs):
                if data and data["name"] == "Плохой":
                    raise RuntimeError("API error")
                return await super().post(endpoint, data, **kwargs)

        client = FailingClient()
        endpoint = TaxonomiesEndpoint(client, "categories")

        term_ids, primary = await endpoint.process_taxonomy_string("Плохой; Хороший")

        assert len(term_ids) == 1
        # Первая спецификация не разрешилась — primary остаётся пустым
        assert primary is None

    async def test_hierarchy_on_flat_taxonomy_raises(self):
        """Неиерархическая таксономия: WP игнорирует parent → ошибка, не дубликаты."""

        class FlatTaxonomyClient(FakeTermsClient):
            """WordPress неиерархической таксономии игнорирует parent при создании."""

            async def post(self, endpoint, data=None, **kwargs):
                data = dict(data or {})
                data.pop("parent", None)
                return await super().post(endpoint, data, **kwargs)

        client = FlatTaxonomyClient()
        endpoint = TaxonomiesEndpoint(client, "genre_tag")

        with pytest.raises(ValidationError, match="неиерархическая"):
            await endpoint.process_taxonomy_string("A>B")

        # Ошибка ловится сразу, до накопления дубликатов на следующих запусках
        assert [t["name"] for t in client.terms] == ["A", "B"]

    async def test_malformed_hierarchy_propagates(self):
        endpoint = TaxonomiesEndpoint(FakeTermsClient(), "categories")

        with pytest.raises(ValidationError):
            await endpoint.process_taxonomy_string("a>; b")


# ── Кэш при иерархии ────────────────────────────────────────────────


class TestHierarchyWithCache:
    async def test_cached_hierarchy_avoids_repeated_api_calls(self):
        client = FakeTermsClient()
        cache = TaxonomyCache()
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        first, _ = await endpoint.process_taxonomy_string("Родитель>Ребёнок", cache)
        created_after_first = len(client.created)
        second, _ = await endpoint.process_taxonomy_string("Родитель>Ребёнок", cache)

        assert first == second
        assert len(client.created) == created_after_first

    async def test_child_does_not_shadow_root_in_flat_cache(self):
        client = FakeTermsClient(
            [
                {"id": 1, "name": "Родитель", "parent": 0},
                {"id": 2, "name": "Общий", "parent": 0},
            ]
        )
        cache = TaxonomyCache()
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        # Дочерний "Общий" создаётся под "Родитель" и попадает в иерархический индекс
        nested_ids, _ = await endpoint.process_taxonomy_string("Родитель>Общий", cache)
        # Плоский поиск того же имени должен по-прежнему находить корневой термин
        flat_ids, _ = await endpoint.process_taxonomy_string("Общий", cache)

        assert flat_ids == [2]
        assert nested_ids != flat_ids


# ── dry_run: не создаёт термины на сайте ────────────────────────────


class TestProcessTaxonomyStringDryRun:
    async def test_missing_term_not_created(self):
        """Несуществующий термин под dry_run не создаётся, возвращается ID=0."""
        client = FakeTermsClient()
        endpoint = TaxonomiesEndpoint(client, "categories")

        term_ids, primary = await endpoint.process_taxonomy_string("Новая категория", dry_run=True)

        assert client.created == []
        assert term_ids == [0]
        assert primary == 0

    async def test_existing_term_still_found(self):
        """Уже существующий термин находится под dry_run как обычно (без create)."""
        client = FakeTermsClient([{"id": 5, "name": "Портьеры", "parent": 0}])
        endpoint = TaxonomiesEndpoint(client, "categories")

        term_ids, primary = await endpoint.process_taxonomy_string("Портьеры", dry_run=True)

        assert client.created == []
        assert term_ids == [5]
        assert primary == 5

    async def test_mixed_existing_and_missing(self):
        """Смесь существующего и нового термина: create только не бывает вовсе."""
        client = FakeTermsClient([{"id": 5, "name": "Портьеры", "parent": 0}])
        endpoint = TaxonomiesEndpoint(client, "categories")

        term_ids, primary = await endpoint.process_taxonomy_string("Портьеры; Новая", dry_run=True)

        assert client.created == []
        assert term_ids == [5, 0]
        assert primary == 5

    async def test_hierarchy_missing_ancestors_not_created(self):
        """Иерархия с несуществующими предками: под dry_run ничего не создаётся."""
        client = FakeTermsClient()
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        term_ids, primary = await endpoint.process_taxonomy_string("Родитель>Ребёнок", dry_run=True)

        assert client.created == []
        assert client.terms == []
        assert term_ids == [0]
        assert primary == 0

    async def test_hierarchy_existing_ancestor_still_found(self):
        """Существующий предок находится, только недостающий потомок — placeholder."""
        client = FakeTermsClient([{"id": 7, "name": "Родитель", "parent": 0}])
        endpoint = TaxonomiesEndpoint(client, "services_cat")

        term_ids, primary = await endpoint.process_taxonomy_string("Родитель>Ребёнок", dry_run=True)

        assert client.created == []
        assert term_ids == [0]
        assert primary == 0

    async def test_dry_run_does_not_pollute_cache(self):
        """Плейсхолдер отсутствующего термина не кэшируется как настоящий."""
        client = FakeTermsClient()
        cache = TaxonomyCache()
        endpoint = TaxonomiesEndpoint(client, "categories")

        first_ids, _ = await endpoint.process_taxonomy_string("Новая", taxonomy_cache=cache, dry_run=True)
        assert first_ids == [0]
        assert client.created == []
        # Кэш не должен думать, что термин уже существует под ID=0
        assert cache.find("categories", "Новая") is None

        # Следующий (уже не dry_run) прогон реально создаёт термин
        second_ids, _ = await endpoint.process_taxonomy_string("Новая", taxonomy_cache=cache, dry_run=False)
        assert len(client.created) == 1
        assert second_ids != [0]
        assert cache.find("categories", "Новая") is not None


# ── normalize_taxonomy_name (регресс) ───────────────────────────────


class TestNormalizeTaxonomyName:
    def test_standard(self):
        assert normalize_taxonomy_name("category") == "categories"
        assert normalize_taxonomy_name("tag") == "tags"
        assert normalize_taxonomy_name("post_tag") == "tags"

    def test_custom_unchanged(self):
        assert normalize_taxonomy_name("rb_services_cat") == "rb_services_cat"
