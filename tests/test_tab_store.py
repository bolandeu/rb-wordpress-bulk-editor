"""
Хранилище табов (`records/store.py`) и команды на нём: options, fetch-media.

Сайт подменён: проверяется, что команда читает/пишет таб выбранного носителя
и что уходит в WordPress, а не REST API.
"""

import json

import pytest
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from src.cli import app, fetch_commands, tool_commands
from src.exceptions import ValidationError
from src.records.store import ExcelTabStore, JsonTabStore, open_store

runner = CliRunner()

SITE_OPTIONS = {"schema": "Organization", "site_type": "logistics", "search": 1}

MEDIA = [
    {
        "id": 7,
        "date": "2026-01-01T00:00:00",
        "modified": "2026-01-02T00:00:00",
        "title": {"rendered": "Фото"},
        "type": "attachment",
        "slug": "foto",
        "link": "https://site.ru/foto",
        "status": "inherit",
        "guid": {"rendered": "https://site.ru/foto.jpg"},
        "caption": {"rendered": ""},
        "alt_text": "alt",
        "media_type": "image",
        "mime_type": "image/jpeg",
        "post": 10,
        "source_url": "https://site.ru/foto.jpg",
        "description": {"rendered": ""},
    }
]


def run(*args):
    return runner.invoke(app, ["--json", *args])


def payload(result):
    return json.loads(result.stdout.strip())


def write_doc(path, tabs, project="mw"):
    path.write_text(json.dumps({"project": project, "tabs": tabs}, ensure_ascii=False), encoding="utf-8")
    return path


def read_doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def fake_site(monkeypatch):
    """Доступы есть, сайт отдаёт SITE_OPTIONS/MEDIA и запоминает отправленное."""
    sent = []

    async def fetch_options(wp_config):
        return dict(SITE_OPTIONS)

    async def update_options(wp_config, site_options):
        sent.append(site_options)

    async def fetch_media(wp_config):
        return fetch_commands._media_records(MEDIA)

    credentials = lambda **kwargs: ("https://site.ru", "admin", "secret")  # noqa: E731
    for module in (tool_commands, fetch_commands):
        monkeypatch.setattr(module, "load_and_print_credentials", credentials)
    monkeypatch.setattr(tool_commands, "_fetch_options", fetch_options)
    monkeypatch.setattr(tool_commands, "_update_options", update_options)
    monkeypatch.setattr(fetch_commands, "_fetch_media_records", fetch_media)
    return sent


# ── Хранилище ────────────────────────────────────────────────────────


class TestStore:
    def test_format_by_extension(self, tmp_path):
        assert isinstance(open_store(tmp_path / "a.json"), JsonTabStore)
        assert isinstance(open_store(tmp_path / "a.XLSX"), ExcelTabStore)
        with pytest.raises(ValidationError, match="Неизвестный формат"):
            open_store(tmp_path / "a.csv")

    @pytest.mark.parametrize("name", ["data.json", "data.xlsx"])
    def test_roundtrip_keeps_other_tabs(self, tmp_path, name):
        store = open_store(tmp_path / name, project="mw")
        assert store.read_tab("options") is None

        store.write_tab("company", [{"key": "name", "value": "МВ"}])
        store.write_tab("options", [{"key": "a", "value": 1}, {"key": "b", "value": "x"}])
        store.write_tab("options", [{"key": "c", "value": "y"}])

        reread = open_store(tmp_path / name)
        assert [{k: v for k, v in r.items() if k != "_row_num"} for r in reread.read_tab("options")] == [
            {"key": "c", "value": "y"}
        ]
        assert reread.read_tab("company")[0]["value"] == "МВ"

    def test_json_numbers_rows_and_keeps_project(self, tmp_path):
        path = tmp_path / "data.json"
        JsonTabStore(path, project="mw").write_tab("media", [{"id": 1}, {"_row_num": 99, "id": 2}])
        document = read_doc(path)
        assert document["project"] == "mw"
        assert document["tabs"]["media"] == [{"_row_num": 2, "id": 1}, {"_row_num": 3, "id": 2}]

    def test_excel_keeps_sheet_position_and_skips_row_num(self, tmp_path):
        path = tmp_path / "data.xlsx"
        workbook = Workbook()
        workbook.active.title = "posts"
        workbook.create_sheet("options")
        workbook.create_sheet("pages")
        workbook.save(path)

        ExcelTabStore(path).write_tab("options", [{"_row_num": 2, "key": "k", "value": ["a", "b"]}])

        workbook = load_workbook(path)
        assert workbook.sheetnames == ["posts", "options", "pages"]
        rows = list(workbook["options"].iter_rows(values_only=True))
        assert rows == [("key", "value"), ("k", '["a", "b"]')]


# ── options ──────────────────────────────────────────────────────────


class TestOptions:
    def test_fetch_to_json_tab(self, fake_site, tmp_path):
        out = write_doc(tmp_path / "site.json", {"site.ru": [{"_row_num": 2, "type": "post", "title": "t"}]})

        result = run("options", "--fetch", "--file", str(out))

        assert result.exit_code == 0, result.stdout
        data = payload(result)
        assert (data["format"], data["tab"], data["records"]) == ("json", "options", 3)
        tabs = read_doc(out)["tabs"]
        assert tabs["options"][0] == {"_row_num": 2, "key": "schema", "value": "Organization"}
        assert tabs["site.ru"][0]["title"] == "t"

    def test_fetch_to_xlsx(self, fake_site, tmp_path):
        out = tmp_path / "site.xlsx"
        result = run("options", "--fetch", "--file", str(out))

        assert result.exit_code == 0, result.stdout
        assert payload(result)["format"] == "xlsx"
        rows = list(load_workbook(out)["options"].iter_rows(values_only=True))
        assert rows[0] == ("key", "value") and ("search", 1) in rows

    def test_fetch_existing_tab_needs_confirmation(self, fake_site, tmp_path):
        out = write_doc(tmp_path / "site.json", {"options": [{"_row_num": 2, "key": "old", "value": "1"}]})

        refused = run("--non-interactive", "options", "--fetch", "--file", str(out))
        assert refused.exit_code == 2
        assert read_doc(out)["tabs"]["options"][0]["key"] == "old"

        assert run("options", "--fetch", "--file", str(out), "--confirm").exit_code == 0

    def test_fetch_refuses_project_registry(self, fake_site, tmp_path):
        project = tmp_path / "mw"
        project.mkdir()
        registry = write_doc(project / "mw.json", {"options": []})

        result = runner.invoke(app, ["-P", str(project), "--json", "options", "--fetch", "--file", str(registry)])

        assert result.exit_code == 2
        assert "реестр" in payload(result)["error"]["message"]

    def test_update_from_registry_tab(self, fake_site, tmp_path):
        registry = write_doc(
            tmp_path / "mw.json",
            {
                "options": [
                    {"_row_num": 2, "key": "phone", "label": "Телефон", "value": " +7 000 "},
                    {"_row_num": 3, "key": "empty", "label": "Пусто", "value": ""},
                    {"_row_num": 4, "key": "", "value": "без ключа"},
                ],
                "mw.ru": [],
            },
        )

        dry = run("options", "--file", str(registry), "--dry-run")
        assert dry.exit_code == 0 and fake_site == []
        assert payload(dry)["options"] == {"phone": "+7 000"}

        result = run("options", "--file", str(registry), "--confirm")
        assert result.exit_code == 0, result.stdout
        assert fake_site == [{"phone": "+7 000"}]

    def test_update_needs_confirmation(self, fake_site, tmp_path):
        registry = write_doc(tmp_path / "mw.json", {"options": [{"key": "a", "value": "b"}]})
        result = run("--non-interactive", "options", "--file", str(registry))
        assert result.exit_code == 2 and fake_site == []

    def test_update_from_legacy_excel_header(self, fake_site, tmp_path):
        path = tmp_path / "site.xlsx"
        workbook = Workbook()
        workbook.active.title = "options"
        workbook.active.append(["option", "value"])
        workbook.active.append(["posts_per_page", 10])
        workbook.save(path)

        result = run("options", "--file", str(path), "--confirm")

        assert result.exit_code == 0, result.stdout
        assert fake_site == [{"posts_per_page": 10}]

    def test_update_without_options_tab(self, fake_site, tmp_path):
        registry = write_doc(tmp_path / "mw.json", {"mw.ru": []})
        result = run("options", "--file", str(registry), "--confirm")
        assert result.exit_code == 2
        assert "options" in payload(result)["error"]["message"]


# ── fetch-media ──────────────────────────────────────────────────────


class TestFetchMedia:
    def test_to_json_tab(self, fake_site, tmp_path):
        out = write_doc(tmp_path / "site.json", {"options": [{"key": "a", "value": "b"}]})

        result = run("fetch-media", "--file", str(out))

        assert result.exit_code == 0, result.stdout
        data = payload(result)
        assert (data["format"], data["tab"], data["records"]) == ("json", "media", 1)
        record = read_doc(out)["tabs"]["media"][0]
        assert record["_row_num"] == 2 and record["id"] == 7 and record["title"] == "Фото"
        assert "type" not in record  # иначе запись таба выглядела бы контентом типа attachment
        assert read_doc(out)["tabs"]["options"] == [{"key": "a", "value": "b"}]

    def test_to_xlsx_sheet(self, fake_site, tmp_path):
        out = tmp_path / "site.xlsx"
        result = run("fetch-media", "--file", str(out))

        assert result.exit_code == 0, result.stdout
        rows = list(load_workbook(out)["media"].iter_rows(values_only=True))
        assert rows[0][:3] == ("action", "img_path", "id") and rows[1][2] == 7

    def test_existing_tab_needs_confirmation(self, fake_site, tmp_path):
        out = write_doc(tmp_path / "site.json", {"media": [{"_row_num": 2, "id": 1}]})
        assert run("--non-interactive", "fetch-media", "--file", str(out)).exit_code == 2
        assert run("fetch-media", "--file", str(out), "--confirm").exit_code == 0
