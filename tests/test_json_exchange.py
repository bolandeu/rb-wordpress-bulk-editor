"""
Обратное направление обмена для JSON: update / fetch / validate.

WordPress подменяется фейковым клиентом, credentials — заглушкой: проверяется
контракт CLI (что пишется в файл, какие ошибки и когда), а не HTTP.
"""

import json

import pytest
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from rb_wordpress_bulk_editor.cli import app
from rb_wordpress_bulk_editor.cli import fetch_commands, tool_commands, update_commands
from rb_wordpress_bulk_editor.exceptions import AuthenticationError

runner = CliRunner()

SITE_TAB = "example.ru"


# ── Фейковый WordPress ───────────────────────────────────────────────


class FakeSite:
    """Данные сайта: {rest-путь: [объекты]} + типы/таксономии для discovery."""

    def __init__(self):
        self.items = {
            "rb_services": [
                {
                    "id": 10,
                    "type": "rb_services",
                    "slug": "portery",
                    "status": "publish",
                    "title": {"rendered": "Портьеры (WP)"},
                    "content": {"rendered": "<p>x</p>"},
                    "excerpt": {"rendered": ""},
                    "link": "https://s/portery",
                    "parent": 0,
                    "rb_services_cat": [5],
                    "acf": {"f1": "портьеры", "group": {"inner": "в"}, "phones": [{"n": "1"}]},
                    "seo": {
                        "title": "SEO WP",
                        "robots": {"index": True},
                        "schema": {"graph": {"@graph": [{"@type": "WebPage"}]}},
                    },
                },
            ],
            "posts": [
                {
                    "id": 20,
                    "type": "post",
                    "slug": "news",
                    "status": "draft",
                    "title": {"rendered": "Новость"},
                    "content": {"rendered": ""},
                    "excerpt": {"rendered": ""},
                    "categories": [],
                    "tags": [],
                },
            ],
            "rb_services_cat": [
                {
                    "id": 5,
                    "name": "Импорт из Китая",
                    "slug": "import",
                    "description": "Текст",
                    "parent": 0,
                    "taxonomy": "rb_services_cat",
                    "acf": {"icon": "box"},
                },
            ],
        }
        self.types = {"post": {"name": "Posts"}, "rb_services": {"name": "Услуги"}}
        self.taxonomies = {"rb_services_cat": {"name": "Категории услуг"}}

    def get(self, endpoint, params=None):
        path = endpoint.removeprefix("/wp-json/wp/v2/")
        if path == "types":
            return self.types
        if path == "taxonomies":
            return self.taxonomies
        if (params or {}).get("page", 1) > 1:
            return []
        if "/" in path:
            base, item_id = path.rsplit("/", 1)
            for item in self.items.get(base, []):
                if item["id"] == int(item_id):
                    return item
            from rb_wordpress_bulk_editor.exceptions import NotFoundError

            raise NotFoundError(f"{endpoint} не найден")
        return list(self.items.get(path, []))


class FakeClient:
    site = None
    instances = []

    def __init__(self, config=None, **kwargs):
        self.requests = []
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def test_connection(self, *args, **kwargs):
        return True

    async def get(self, endpoint, params=None, **kwargs):
        self.requests.append(("GET", endpoint, params))
        return FakeClient.site.get(endpoint, params)

    async def post(self, *args, **kwargs):  # pragma: no cover - update/fetch не пишут на сайт
        raise AssertionError("WordPress не должен изменяться")

    put = delete = post


def _credentials(**kwargs):
    return ("https://example.ru", "admin", "secret")


def _no_credentials(**kwargs):
    # Доступов нет — validate проверяет без сайта
    raise AuthenticationError("нет доступов")


@pytest.fixture
def fake_wp(monkeypatch):
    FakeClient.site = FakeSite()
    FakeClient.instances = []
    for module in (update_commands, fetch_commands):
        monkeypatch.setattr(module, "load_and_print_credentials", _credentials)
        monkeypatch.setattr(module, "WordPressClient", FakeClient)
    monkeypatch.setattr(tool_commands, "load_and_print_credentials", _no_credentials)
    return FakeClient.site


def write_doc(path, tabs, project="mw"):
    path.write_text(json.dumps({"project": project, "tabs": tabs}, ensure_ascii=False), encoding="utf-8")
    return path


def read_tab(path, tab=SITE_TAB):
    return json.loads(path.read_text(encoding="utf-8"))["tabs"][tab]


def run(*args):
    return runner.invoke(app, ["--json", *args])


def payload(result):
    return json.loads(result.stdout.strip())


@pytest.fixture
def project_json(tmp_path):
    """Реестр в духе antagor: служебный таб + смешанный таб сайта."""
    return write_doc(
        tmp_path / "data.json",
        {
            "options": [{"_row_num": 2, "key": "phone", "value": "+7"}],
            SITE_TAB: [
                {
                    "_row_num": 2,
                    "action": "skip",
                    "id": "10",
                    "type": "rb_services",
                    "taxonomy": "",
                    "title": "Портьеры",
                    "seo.title": "",
                    "acf.f1": "",
                },
                {
                    "_row_num": 3,
                    "action": "skip",
                    "id": 5,
                    "type": "",
                    "taxonomy": "rb_services_cat",
                    "name": "Старое имя",
                    "description": "",
                },
                {"_row_num": 4, "action": "add", "type": "rb_services", "title": "Без id"},
            ],
        },
    )


# ── update ───────────────────────────────────────────────────────────


class TestUpdateJson:
    def test_pulls_fields_for_content_and_terms(self, fake_wp, project_json):
        result = run(
            "update",
            "--file",
            str(project_json),
            "--sheets",
            SITE_TAB,
            "--columns",
            "title,seo.title,acf.f1,name,seo.robots.index",
        )

        assert result.exit_code == 0, result.stdout
        content, term, no_id = read_tab(project_json)
        assert content["title"] == "Портьеры (WP)"
        assert content["seo.title"] == "SEO WP"
        assert content["acf.f1"] == "портьеры"
        assert content["seo.robots.index"] is True  # поля не было — в JSON добавляется
        assert term["name"] == "Импорт из Китая"  # термин — через endpoint таксономии
        assert "id" not in no_id and no_id["title"] == "Без id"
        assert read_tab(project_json, "options") == [{"_row_num": 2, "key": "phone", "value": "+7"}]
        assert payload(result)["updated"] == 2

    def test_id_filter(self, fake_wp, project_json):
        result = run("update", "--file", str(project_json), "--sheets", SITE_TAB, "--columns", "name", "--id", "5")

        assert result.exit_code == 0, result.stdout
        assert payload(result)["found"] == 1
        assert read_tab(project_json)[0]["title"] == "Портьеры"

    def test_dry_run_does_not_touch_the_file(self, fake_wp, project_json):
        before = project_json.read_bytes()

        result = run(
            "update", "--file", str(project_json), "--sheets", SITE_TAB, "--columns", "title,name", "--dry-run"
        )

        assert result.exit_code == 0, result.stdout
        assert payload(result)["updated"] == 2
        assert project_json.read_bytes() == before

    def test_sheets_required_before_credentials(self, fake_wp, project_json, monkeypatch):
        monkeypatch.setattr(update_commands, "load_and_print_credentials", _no_credentials)

        result = run("update", "--file", str(project_json), "--columns", "title")

        assert result.exit_code == 2
        assert "Доступные табы: options, " + SITE_TAB in payload(result)["error"]["message"]

    def test_missing_on_site_is_a_warning(self, fake_wp, project_json):
        fake_wp.items["rb_services"] = []

        result = run("update", "--file", str(project_json), "--sheets", SITE_TAB, "--columns", "title")

        assert result.exit_code == 0, result.stdout
        assert any("404" in w for w in payload(result)["warnings"])


class TestUpdateExcel:
    def test_excel_updates_existing_columns_only(self, fake_wp, tmp_path):
        path = tmp_path / "site.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "rb_services"
        ws.append(["id", "action", "title", "slug"])
        ws.append([10, "skip", "Старое", "portery"])
        wb.save(path)

        result = run("update", "--file", str(path), "--columns", "title,seo.title")

        assert result.exit_code == 0, result.stdout
        rows = list(load_workbook(path)["rb_services"].values)
        assert rows[0] == ("id", "action", "title", "slug")  # колонка seo.title не создана
        assert rows[1][2] == "Портьеры (WP)"


# ── fetch ────────────────────────────────────────────────────────────


class TestFetchJson:
    def test_one_tab_per_site_terms_first(self, fake_wp, tmp_path):
        out = tmp_path / "wp-fetch" / "site.json"

        result = run("fetch", "--file", str(out))

        assert result.exit_code == 0, result.stdout
        data = payload(result)
        assert data["tab"] == SITE_TAB  # домен сайта
        records = read_tab(out)
        assert [r["_row_num"] for r in records] == [2, 3, 4]
        term = records[0]
        assert term["taxonomy"] == "rb_services_cat" and "type" not in term
        assert (term["name"], term["description"], term["acf.icon"]) == ("Импорт из Китая", "Текст", "box")
        by_type = {r.get("type"): r for r in records[1:]}
        assert set(by_type) == {"rb_services", "posts"}  # REST-имя, а не post/page
        service = by_type["rb_services"]
        assert service["acf.f1"] == "портьеры" and service["acf.group.inner"] == "в"
        assert service["seo.robots.index"] is True
        assert service["rb_services_cat"] == "Импорт из Китая"  # id термина → имя
        assert not any(k.startswith("acf_") for k in service)
        assert not any(k.startswith("seo.schema") for k in service)
        assert "acf.phones" not in service  # повторитель ACF — не табличное значение
        assert "categories" not in service and "categories" in by_type["posts"]
        assert json.loads(out.read_text(encoding="utf-8"))["project"] is None

    def test_default_path_under_project(self, fake_wp, tmp_path):
        project = tmp_path / "mw"
        project.mkdir()
        (project / ".env").write_text("", encoding="utf-8")

        result = runner.invoke(app, ["-P", str(project), "--json", "fetch", "--to-json"])

        assert result.exit_code == 0, result.stdout
        out = project / "wp-fetch" / f"{SITE_TAB}.json"
        assert payload(result)["file"] == str(out)
        assert json.loads(out.read_text(encoding="utf-8"))["project"] == "mw"

    def test_refuses_project_registry(self, fake_wp, tmp_path, monkeypatch):
        monkeypatch.setattr(fetch_commands, "load_and_print_credentials", _no_credentials)
        project = tmp_path / "mw"
        project.mkdir()
        registry = write_doc(project / "mw.json", {SITE_TAB: []})
        before = registry.read_bytes()

        result = runner.invoke(app, ["-P", str(project), "--json", "fetch", "--file", str(registry)])

        assert result.exit_code == 2
        assert "реестр проекта" in payload(result)["error"]["message"]
        assert registry.read_bytes() == before

    def test_existing_tab_needs_confirmation(self, fake_wp, tmp_path):
        out = write_doc(tmp_path / "site.json", {SITE_TAB: [{"_row_num": 2, "type": "posts"}], "other": [1]})

        refused = run("--non-interactive", "fetch", "--file", str(out))
        assert refused.exit_code == 2
        assert read_tab(out) == [{"_row_num": 2, "type": "posts"}]

        result = run("fetch", "--file", str(out), "--confirm")
        assert result.exit_code == 0, result.stdout
        assert len(read_tab(out)) == 3  # пересобран целиком, не дописан
        assert read_tab(out, "other") == [1]  # остальные табы не тронуты

    def test_refetch_is_idempotent(self, fake_wp, tmp_path):
        out = tmp_path / "site.json"
        run("fetch", "--file", str(out))
        first = out.read_bytes()

        run("fetch", "--file", str(out), "--confirm")

        assert out.read_bytes() == first

    def test_tab_override(self, fake_wp, tmp_path):
        out = tmp_path / "site.json"

        result = run("fetch", "--file", str(out), "--tab", "example.local")

        assert result.exit_code == 0, result.stdout
        assert len(read_tab(out, "example.local")) == 3

    def test_fetched_tab_passes_validate(self, fake_wp, tmp_path):
        out = tmp_path / "site.json"
        run("fetch", "--file", str(out))

        result = run("validate", "--file", str(out), "--sheets", SITE_TAB)

        assert result.exit_code == 0, result.stdout


# ── validate ─────────────────────────────────────────────────────────


class TestValidateJson:
    def test_all_problems_reported_without_credentials(self, fake_wp, tmp_path):
        path = write_doc(
            tmp_path / "data.json",
            {
                SITE_TAB: [
                    {"_row_num": 2, "type": "posts", "taxonomy": "category"},
                    {"_row_num": 2, "type": "posts"},
                    {"_row_num": 3},
                    {"_row_num": 4, "action": "u", "id": 1, "type": "posts", "acf.more": "content/404.html"},
                ]
            },
        )

        result = run("validate", "--file", str(path), "--sheets", SITE_TAB)

        assert result.exit_code == 2
        message = payload(result)["error"]["message"]
        assert "ошибок — 4" in message
        for fragment in ("одновременно", "уже занят", "ни 'type'", "404.html"):
            assert fragment in message

    def test_broken_file_on_skip_record_is_a_warning(self, fake_wp, tmp_path):
        (tmp_path / "content").mkdir()
        (tmp_path / "content" / "1.html").write_text("<p>ok</p>", encoding="utf-8")
        path = write_doc(
            tmp_path / "data.json",
            {
                SITE_TAB: [
                    {"_row_num": 2, "action": "add", "type": "posts", "title": "a", "acf.more": "content/1.html"},
                    {"_row_num": 3, "action": "skip", "id": 2, "type": "posts", "acf.more": "content/2.html"},
                    {"_row_num": 4, "action": "publish", "id": 3, "type": "posts"},
                ]
            },
        )

        result = run("validate", "--file", str(path), "--sheets", SITE_TAB)

        assert result.exit_code == 0, result.stdout
        warnings = payload(result)["tabs"][0]["warnings"]
        assert len(warnings) == 2
        assert "2.html" in warnings[0] and "неизвестный action" in warnings[1]

    def test_sheets_required(self, fake_wp, project_json):
        result = run("validate", "--file", str(project_json))

        assert result.exit_code == 2
        assert "--sheets" in payload(result)["error"]["message"]


class TestValidateWithSite:
    """Есть доступы — таксономии и типы сверяются с сайтом."""

    @pytest.fixture
    def online(self, fake_wp, monkeypatch):
        from rb_wordpress_bulk_editor.wordpress.site_schema import SiteSchema

        schema = SiteSchema.from_rest(
            {"rb_topic": {"slug": "rb_topic", "rest_base": "rb_topic", "types": ["rb_services"]}},
            {"rb_services": {"slug": "rb_services", "rest_base": "rb_services"}},
        )
        state = {"calls": 0}

        async def fake_schema(wp_config):
            state["calls"] += 1
            return schema

        monkeypatch.setattr(tool_commands, "load_and_print_credentials", _credentials)
        monkeypatch.setattr(tool_commands, "_load_site_schema", fake_schema)
        return state

    def test_site_taxonomy_is_accepted(self, online, tmp_path):
        path = write_doc(
            tmp_path / "data.json",
            {
                SITE_TAB: [
                    {"_row_num": 2, "action": "add", "type": "rb_services", "title": "A", "rb_topic": "Логистика"},
                ]
            },
        )

        result = run("validate", "--file", str(path), "--sheets", SITE_TAB)

        assert result.exit_code == 0, result.stdout
        assert payload(result)["site_checked"] is True

    def test_unknown_type_found(self, online, tmp_path):
        path = write_doc(
            tmp_path / "data.json",
            {
                SITE_TAB: [
                    {"_row_num": 2, "action": "add", "type": "rb_servces", "title": "A"},
                ]
            },
        )

        result = run("validate", "--file", str(path), "--sheets", SITE_TAB)

        assert result.exit_code == 2
        assert "rb_services" in payload(result)["error"]["message"]

    def test_offline_flag_does_not_touch_the_site(self, online, tmp_path):
        path = write_doc(
            tmp_path / "data.json",
            {
                SITE_TAB: [
                    {"_row_num": 2, "action": "add", "type": "rb_servces", "title": "A"},
                ]
            },
        )

        result = run("validate", "--file", str(path), "--sheets", SITE_TAB, "--offline")

        assert result.exit_code == 0, result.stdout
        assert payload(result)["site_checked"] is False
        assert online["calls"] == 0

    def test_site_down_is_an_error_not_a_silent_offline(self, online, monkeypatch, tmp_path):
        from rb_wordpress_bulk_editor.exceptions import NetworkError

        async def down(wp_config):
            raise NetworkError("сайт не ответил")

        monkeypatch.setattr(tool_commands, "_load_site_schema", down)
        path = write_doc(
            tmp_path / "data.json",
            {
                SITE_TAB: [
                    {"_row_num": 2, "action": "add", "type": "rb_services", "title": "A"},
                ]
            },
        )

        result = run("validate", "--file", str(path), "--sheets", SITE_TAB)

        assert result.exit_code == 5


# ── Термин: fetch → правка → sync ────────────────────────────────────


class TestTermPayloadRoundTrip:
    """Термин из общего JSON-таба должен уходить на сайт вложенными acf/seo."""

    @staticmethod
    def build(data, tmp_path):
        from unittest.mock import MagicMock
        from rb_wordpress_bulk_editor.sync.taxonomy_sync import TaxonomySync

        config = MagicMock()
        config.paths.excel_file = tmp_path / "data.json"
        return TaxonomySync(MagicMock(), config)._build_payload(data)

    def test_fetched_term_goes_back_nested(self, tmp_path):
        from rb_wordpress_bulk_editor.records.json_fetch import term_to_record
        from rb_wordpress_bulk_editor.records.json_source import SOURCE_META_FIELDS

        record = term_to_record(
            {
                "id": 5,
                "name": "Импорт",
                "slug": "import",
                "description": "",
                "parent": 0,
                "acf": {"icon": "box"},
                "seo": {"title": "SEO", "robots": {"index": False}},
            },
            "rb_services_cat",
        )
        record["acf.icon"] = "ship"
        data = {k: v for k, v in record.items() if k not in SOURCE_META_FIELDS}

        payload = self.build(data, tmp_path)

        assert payload["acf"] == {"icon": "ship"}
        assert payload["seo"] == {"title": "SEO", "robots": {"index": False}}
        assert not any("." in key for key in payload)

    def test_blank_columns_of_mixed_tab_are_not_sent(self, tmp_path):
        payload = self.build(
            {
                "name": "Термин",
                "slug": "t",
                "acf.f1": "",
                "seo.title": "",
                "seo.robots.index": "",
                "date": "",
                "link": "",
                "title": "",
            },
            tmp_path,
        )

        assert "acf" not in payload
        assert payload["seo"] == {"robots": {"index": True}}  # bool по умолчанию true
        assert "date" not in payload and "link" not in payload

    def test_file_value_and_legacy_acf_merge(self, tmp_path):
        (tmp_path / "t.html").write_text("<p>Текст</p>", encoding="utf-8")

        payload = self.build({"name": "Т", "acf.more": "t.html", "acf_icon": "box"}, tmp_path)

        assert payload["acf"] == {"more": "<p>Текст</p>", "icon": "box"}
