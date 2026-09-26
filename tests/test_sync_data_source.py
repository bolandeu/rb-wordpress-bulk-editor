"""
Tests for выбора источника данных и фильтрации записей в sync/async.

Сайт подменён фейковым клиентом: проверяется, какие записи дошли до API и что
записалось обратно в файл проекта, а не поведение WordPress.

Фикстура JSON повторяет форму реального файла проекта antagor: служебные табы
(`company`, `options`) с записями `{key,label,value}` и контентный таб с
`type`/`taxonomy`, dot-нотацией `seo.*`/`acf.*` и полем таксономии `*_cat`.
"""

import json

import pytest
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from src.cli import app, sync_commands

runner = CliRunner()


# ── Фейковый WordPress ───────────────────────────────────────────────

class FakeWordPressClient:
    """Минимальный клиент: списки пустые, создание выдаёт новый id."""

    instances = []

    def __init__(self, config=None, **kwargs):
        self.config = config
        self.requests = []
        self._next_id = 100
        FakeWordPressClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def test_connection(self):
        return True

    async def get(self, endpoint, params=None):
        self.requests.append(("GET", endpoint, params))
        return []

    async def post(self, endpoint, data=None):
        self.requests.append(("POST", endpoint, data))
        self._next_id += 1
        data = data or {}
        return {
            "id": self._next_id,
            "link": f"https://example.com/{self._next_id}",
            "slug": data.get("slug", ""),
            "name": data.get("name", ""),
            "status": data.get("status", "publish"),
        }

    async def put(self, endpoint, data=None):
        self.requests.append(("PUT", endpoint, data))
        return {"id": 1, "link": "https://example.com/1"}

    async def delete(self, endpoint, params=None):
        self.requests.append(("DELETE", endpoint, params))
        return {"deleted": True}

    def created(self, wp_type: str):
        """Данные POST-запросов к endpoint типа записи/таксономии."""
        endpoint = f"/wp-json/wp/v2/{wp_type}"
        return [data for method, url, data in self.requests
                if method == "POST" and url == endpoint]


@pytest.fixture
def fake_wp(monkeypatch):
    """Подменяет credentials и WordPressClient; отдаёт список созданных клиентов."""
    FakeWordPressClient.instances = []
    monkeypatch.setattr(
        sync_commands,
        "load_and_print_credentials",
        lambda **kwargs: ("https://example.com", "admin", "secret"),
    )
    monkeypatch.setattr(sync_commands, "WordPressClient", FakeWordPressClient)
    return FakeWordPressClient.instances


# ── Фикстуры данных ──────────────────────────────────────────────────

CONTENT_TAB = "site.ru"


@pytest.fixture
def json_project(tmp_path):
    """Проект с JSON-файлом: два служебных таба и один контентный."""
    document = {
        "project": "mw",
        "sheet_id": "fake-sheet-id",
        "tabs": {
            "company": [
                {"_row_num": 2, "key": "name", "label": "Название", "value": "МВ Логистика"},
            ],
            "options": [
                {"_row_num": 2, "key": "phone", "label": "Телефон", "value": "+7 000"},
            ],
            CONTENT_TAB: [
                {
                    "_row_num": 2, "action": "add", "type": "rb_services",
                    "title": "Перевозка грузов", "slug": "gruz", "status": "publish",
                    "seo.title": "Перевозка грузов — SEO", "acf.f1": "да",
                    "rb_services_cat": "Перевозки",
                },
                {
                    "_row_num": 3, "action": "add", "type": "page",
                    "title": "О компании", "slug": "about", "status": "draft",
                    "acf.f1": "нет",
                },
                {
                    "_row_num": 4, "action": "add", "taxonomy": "rb_services_cat",
                    "name": "Перевозки", "slug": "perevozki",
                },
            ],
        },
    }
    path = tmp_path / "mw.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def excel_project(tmp_path):
    """Проект с xlsx: один контентный лист."""
    wb = Workbook()
    ws = wb.active
    ws.title = "posts"
    ws.append(["id", "action", "title", "slug", "status", "acf.f1"])
    ws.append(["", "add", "Первая", "first", "publish", "да"])
    ws.append(["", "add", "Вторая", "second", "draft", "нет"])
    ws.append(["", "add", "Третья", "third", "publish", "нет"])
    path = tmp_path / "export.xlsx"
    wb.save(path)
    return path


def run_sync(*args):
    return runner.invoke(app, ["--json", "sync", *args])


def stats_of(result) -> dict:
    return json.loads(result.stdout.strip())


def read_tab(path, tab=CONTENT_TAB):
    return json.loads(path.read_text(encoding="utf-8"))["tabs"][tab]


def read_sheet_rows(path, sheet="posts"):
    """Значения ячеек листа — чтобы сравнивать содержимое, а не только байты."""
    return [list(row) for row in load_workbook(path)[sheet].values]


# ── Выбор источника по расширению ────────────────────────────────────

class TestSourceSelection:
    def test_json_file_is_synced_through_json_source(self, fake_wp, json_project):
        result = run_sync("--file", str(json_project), "--sheets", CONTENT_TAB)

        assert result.exit_code == 0, result.stdout
        client = fake_wp[0]
        # Каждая запись ушла в свой endpoint: тип берётся из самой записи.
        assert [d["title"] for d in client.created("rb_services")] == ["Перевозка грузов"]
        assert [d["title"] for d in client.created("page")] == ["О компании"]
        # Термины: явная запись таксономии плюс созданный по полю rb_services_cat.
        assert [d["name"] for d in client.created("rb_services_cat")] == ["Перевозки", "Перевозки"]

    def test_json_writeback_puts_id_into_file(self, fake_wp, json_project):
        run_sync("--file", str(json_project), "--sheets", CONTENT_TAB)

        records = read_tab(json_project)
        assert all(r["id"] for r in records)
        # Метаданные источника остались на месте, а не уехали в payload.
        assert [r["_row_num"] for r in records] == [2, 3, 4]
        assert records[0]["type"] == "rb_services"
        assert records[2]["taxonomy"] == "rb_services_cat"
        # Служебные табы не тронуты.
        assert read_tab(json_project, "options")[0] == {
            "_row_num": 2, "key": "phone", "label": "Телефон", "value": "+7 000",
        }

    def test_dry_run_does_not_touch_the_file(self, fake_wp, json_project):
        before = json_project.read_text(encoding="utf-8")

        result = run_sync("--file", str(json_project), "--sheets", CONTENT_TAB, "--dry-run")

        assert result.exit_code == 0, result.stdout
        assert stats_of(result)["overall"]["total"] == 3
        assert json_project.read_text(encoding="utf-8") == before
        assert fake_wp[0].created("rb_services") == []

    def test_dry_run_does_not_touch_the_excel_file(self, fake_wp, excel_project):
        """
        Регрессия: до этого dry-run переписывал xlsx заглушками ответа API
        (id=0, action=skip, затёртые колонки) — прогон «без изменений» портил
        файл проекта.
        """
        before_bytes = excel_project.read_bytes()
        before_rows = read_sheet_rows(excel_project)

        result = run_sync("--file", str(excel_project), "--sheets", "posts", "--dry-run")

        assert result.exit_code == 0, result.stdout
        assert stats_of(result)["sheets"]["posts"]["added"] == 3
        assert read_sheet_rows(excel_project) == before_rows
        assert excel_project.read_bytes() == before_bytes

    def test_json_writeback_survives_failure_midway(self, fake_wp, json_project, monkeypatch):
        """
        У JSON writer свой на таб: результат уже созданных записей должен
        сохраниться, даже если обработка таба упала дальше по ходу.
        """
        async def explode(records, content_sync, taxonomy_sync, writer=None):
            writer.update_from_result(
                records[0].write_key, {"id": 777, "link": "https://example.com/777"}, "add"
            )
            raise RuntimeError("что-то сломалось посреди таба")

        monkeypatch.setattr(sync_commands, "sync_record_groups", explode)

        result = run_sync("--file", str(json_project), "--sheets", CONTENT_TAB)

        assert stats_of(result)["sheets"][CONTENT_TAB]["errors"] == 1
        assert read_tab(json_project)[0]["id"] == 777

    def test_auto_detection_from_project(self, fake_wp, json_project):
        """Без --file файл данных ищется в папке проекта — и json тоже."""
        result = runner.invoke(
            app,
            ["--json", "--project", str(json_project.parent), "sync", "--sheets", CONTENT_TAB],
        )

        assert result.exit_code == 0, result.stdout
        assert stats_of(result)["file"] == str(json_project)

    def test_both_formats_in_project_require_explicit_file(self, fake_wp, json_project, tmp_path):
        (tmp_path / "mw.xlsx").write_bytes(b"")

        result = runner.invoke(
            app, ["--json", "--project", str(tmp_path), "sync", "--sheets", CONTENT_TAB]
        )

        assert result.exit_code == 2
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is False
        assert "--file" in payload["error"]["message"]

    def test_async_command_also_reads_json(self, fake_wp, json_project):
        result = runner.invoke(
            app, ["async", "--file", str(json_project), "--sheets", CONTENT_TAB]
        )

        assert result.exit_code == 0, result.stdout
        assert len(fake_wp[0].created("page")) == 1



class TestAsyncEnvelope:
    """async отдаёт тот же конверт, что sync: обёртка (antagor) пишет его в журнал."""

    @pytest.mark.parametrize("position", ["global", "local"])
    def test_success_envelope_with_stats(self, fake_wp, json_project, position):
        args = ["async", "--file", str(json_project), "--sheets", CONTENT_TAB]
        args = ["--json", *args] if position == "global" else [*args, "--json"]

        result = runner.invoke(app, args)

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout.strip())  # в stdout только конверт
        assert payload["ok"] is True
        assert payload["cli_version"]
        data = payload  # конверт плоский: {"ok": true, ...статистика, "cli_version"}
        assert data["overall"]["total"] == 3
        assert data["sheets"][CONTENT_TAB]["added"] == 3
        assert data["log_file"].endswith(".log")
        assert "duration_seconds" in data

    def test_error_envelope(self, fake_wp, json_project):
        result = runner.invoke(app, ["--json", "async", "--file", str(json_project)])

        assert result.exit_code == 2
        payload = json.loads(result.stdout.strip())
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"

    def test_quiet_prints_nothing(self, fake_wp, json_project):
        result = runner.invoke(
            app, ["async", "--file", str(json_project), "--sheets", CONTENT_TAB, "--quiet"]
        )

        assert result.exit_code == 0, result.stdout
        assert result.stdout.strip() == ""


# ── Выбор таба ───────────────────────────────────────────────────────

class TestTabSelection:
    def test_missing_sheets_lists_available_tabs(self, fake_wp, json_project):
        result = run_sync("--file", str(json_project))

        assert result.exit_code == 2
        message = json.loads(result.stdout.strip())["error"]["message"]
        assert "--sheets" in message
        for tab in ("company", "options", CONTENT_TAB):
            assert tab in message

    def test_tabs_are_listed_without_credentials(self, monkeypatch, json_project):
        """
        «Какие табы в файле» — вопрос к файлу: ответ не должен требовать
        рабочих credentials, поэтому проверка идёт до их загрузки.
        """
        def fail(**kwargs):
            raise AssertionError("credentials не должны запрашиваться")

        monkeypatch.setattr(sync_commands, "load_and_print_credentials", fail)

        result = run_sync("--file", str(json_project))

        assert result.exit_code == 2
        assert CONTENT_TAB in json.loads(result.stdout.strip())["error"]["message"]

    @pytest.mark.parametrize("value", [",", " , ", ""])
    def test_blank_sheets_is_an_argument_error(self, fake_wp, json_project, value):
        """`--sheets ,` не должен быть тихим «ничего не сделал, всё хорошо»."""
        result = run_sync("--file", str(json_project), "--sheets", value)

        assert result.exit_code == 2
        assert "--sheets" in json.loads(result.stdout.strip())["error"]["message"]

    def test_blank_sheets_is_an_argument_error_for_excel(self, fake_wp, excel_project):
        result = run_sync("--file", str(excel_project), "--sheets", ",")

        assert result.exit_code == 2
        assert "--sheets" in json.loads(result.stdout.strip())["error"]["message"]

    def test_unknown_tab_is_reported(self, fake_wp, json_project):
        result = run_sync("--file", str(json_project), "--sheets", "nope")

        assert result.exit_code == 2
        message = json.loads(result.stdout.strip())["error"]["message"]
        assert "nope" in message and CONTENT_TAB in message

    def test_service_tab_fails_with_clear_message(self, fake_wp, json_project):
        """Служебный таб не контент: понятная ошибка, а не стектрейс."""
        result = run_sync("--file", str(json_project), "--sheets", "options")

        assert result.exit_code == 2
        message = json.loads(result.stdout.strip())["error"]["message"]
        assert "options" in message
        assert "type" in message and "taxonomy" in message
        # До сайта дело не дошло.
        assert fake_wp == []

    def test_several_tabs_in_one_run(self, fake_wp, json_project):
        document = json.loads(json_project.read_text(encoding="utf-8"))
        document["tabs"]["second.ru"] = [
            {"_row_num": 2, "action": "add", "type": "page", "title": "Контакты", "slug": "contacts"},
        ]
        json_project.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        result = run_sync("--file", str(json_project), "--sheets", f"{CONTENT_TAB},second.ru")

        assert result.exit_code == 0, result.stdout
        sheets = stats_of(result)["sheets"]
        assert sheets[CONTENT_TAB]["total"] == 3
        assert sheets["second.ru"]["total"] == 1
        # Запись второго таба не потеряла результат первого.
        assert all(r["id"] for r in read_tab(json_project))
        assert read_tab(json_project, "second.ru")[0]["id"]


# ── --filter ─────────────────────────────────────────────────────────

class TestRecordFilter:
    def test_filter_by_type_on_json(self, fake_wp, json_project):
        result = run_sync(
            "--file", str(json_project), "--sheets", CONTENT_TAB,
            "--filter", "type=rb_services",
        )

        assert result.exit_code == 0, result.stdout
        assert fake_wp[0].created("page") == []
        assert len(fake_wp[0].created("rb_services")) == 1
        # Отброшенные фильтром записи остаются в отчёте как пропущенные.
        sheet = stats_of(result)["sheets"][CONTENT_TAB]
        assert sheet["total"] == 3
        assert sheet["added"] == 1
        assert sheet["skipped"] == 2

    def test_filter_by_dot_notation_field_on_json(self, fake_wp, json_project):
        result = run_sync(
            "--file", str(json_project), "--sheets", CONTENT_TAB, "--filter", "acf.f1=нет",
        )

        assert result.exit_code == 0, result.stdout
        assert [d["title"] for d in fake_wp[0].created("page")] == ["О компании"]
        assert fake_wp[0].created("rb_services") == []

    def test_filters_are_anded(self, fake_wp, json_project):
        result = run_sync(
            "--file", str(json_project), "--sheets", CONTENT_TAB,
            "--filter", "type=page", "--filter", "status=publish",
        )

        assert result.exit_code == 0, result.stdout
        assert fake_wp[0].created("page") == []

    def test_filter_on_excel_source(self, fake_wp, excel_project):
        result = run_sync(
            "--file", str(excel_project), "--sheets", "posts",
            "--filter", "status=publish", "--dry-run",
        )

        assert result.exit_code == 0, result.stdout
        sheet = stats_of(result)["sheets"]["posts"]
        assert sheet["added"] == 2
        assert sheet["skipped"] == 1

    def test_filter_on_excel_dot_notation_column(self, fake_wp, excel_project):
        result = run_sync(
            "--file", str(excel_project), "--sheets", "posts",
            "--filter", "acf.f1=да", "--dry-run",
        )

        assert stats_of(result)["sheets"]["posts"]["added"] == 1

    def test_bad_filter_format(self, fake_wp, json_project):
        result = run_sync("--file", str(json_project), "--sheets", CONTENT_TAB, "--filter", "type")

        assert result.exit_code == 2
        assert "field=value" in json.loads(result.stdout.strip())["error"]["message"]


# ── Гейт необратимых удалений ────────────────────────────────────────

class TestDeleteMediaGate:
    """`--delete-media` удаляет вложения с force=True при любом источнике."""

    def test_json_delete_media_requires_confirmation(self, fake_wp, json_project):
        result = runner.invoke(app, [
            "--json", "sync", "--file", str(json_project), "--sheets", CONTENT_TAB,
            "--delete-media", "--non-interactive",
        ])

        assert result.exit_code == 2
        payload = json.loads(result.stdout.strip())
        assert "--delete-media" in payload["error"]["message"]
        # До сайта дело не дошло: гейт срабатывает до подключения.
        assert fake_wp == []

    def test_json_delete_media_with_confirm_proceeds(self, fake_wp, json_project):
        result = runner.invoke(app, [
            "--json", "sync", "--file", str(json_project), "--sheets", CONTENT_TAB,
            "--delete-media", "--non-interactive", "--confirm",
        ])

        assert result.exit_code == 0, result.stdout
        assert len(fake_wp[0].created("page")) == 1

    def test_json_dry_run_is_not_gated(self, fake_wp, json_project):
        result = run_sync(
            "--file", str(json_project), "--sheets", CONTENT_TAB,
            "--delete-media", "--dry-run",
        )

        assert result.exit_code == 0, result.stdout


# ── Формат antagor: короткие action и значения-пути ──────────────────

def write_json_tab(tmp_path, records):
    path = tmp_path / "mw.json"
    document = {"project": "mw", "tabs": {CONTENT_TAB: records}}
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


class TestAntagorStyleRecords:
    def test_short_actions_are_understood(self, fake_wp, tmp_path):
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "a", "type": "posts", "title": "Новая"},
            {"_row_num": 3, "action": "edit", "id": "5", "type": "posts", "title": "Правка"},
            {"_row_num": 4, "action": "e", "id": "6", "type": "posts", "title": "Правка 2"},
            {"_row_num": 5, "action": "0", "id": "7", "type": "posts", "title": "Пропуск"},
            {"_row_num": 6, "action": "s", "id": "8", "type": "posts", "title": "Пропуск 2"},
        ])

        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB)

        assert result.exit_code == 0, result.stdout
        client = fake_wp[0]
        assert [d["title"] for d in client.created("posts")] == ["Новая"]
        updated = [url for method, url, _ in client.requests if method == "PUT"]
        assert updated == ["/wp-json/wp/v2/posts/5", "/wp-json/wp/v2/posts/6"]

    def test_file_path_value_is_replaced_with_file_content(self, fake_wp, tmp_path):
        (tmp_path / "content-write").mkdir()
        (tmp_path / "content-write" / "1.html").write_text("<p>Относительный</p>", encoding="utf-8")
        absolute = tmp_path / "abs.md"
        absolute.write_text("**Абсолютный**", encoding="utf-8")
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "a", "type": "posts", "title": "Статья",
             "slug": "page.html", "acf.more": "content-write/1.html", "acf.extra": str(absolute)},
        ])

        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB)

        assert result.exit_code == 0, result.stdout
        created = fake_wp[0].created("posts")[0]
        assert created["acf"]["more"] == "<p>Относительный</p>"
        assert created["acf"]["extra"] == "<p><strong>Абсолютный</strong></p>"
        assert created["slug"] == "page.html"  # идентификатор, не файл

    def test_missing_file_is_a_record_error_not_a_string(self, fake_wp, tmp_path):
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "a", "type": "posts", "title": "Статья",
             "acf.more": "content-write/404.html"},
        ])

        # Проверка перед отправкой остановила бы прогон целиком — здесь
        # проверяется поведение самого sync, когда её отключили.
        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB, "--skip-checks")

        assert stats_of(result)["overall"]["errors"] == 1
        assert fake_wp[0].created("posts") == []


# ── Проверка перед отправкой ─────────────────────────────────────────

class TestPreflight:
    def test_typo_in_field_blocks_sync_before_any_request(self, fake_wp, tmp_path):
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "add", "type": "posts", "title": "A", "seo.descripton": "d"},
        ])
        before = path.read_text(encoding="utf-8")

        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB)

        assert result.exit_code == 2, result.stdout
        message = stats_of(result)["error"]["message"]
        assert "seo.descripton" in message and "seo.description" in message
        assert fake_wp == []  # к сайту даже не подключались
        assert path.read_text(encoding="utf-8") == before

    def test_dry_run_is_blocked_too(self, fake_wp, tmp_path):
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "update", "type": "posts", "title": "A"},
        ])

        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB, "--dry-run")

        assert result.exit_code == 2
        assert "id" in stats_of(result)["error"]["message"]

    def test_skip_checks_lets_the_run_through(self, fake_wp, tmp_path):
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "add", "type": "posts", "title": "A", "seo.descripton": "d"},
        ])

        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB, "--skip-checks")

        assert result.exit_code == 0, result.stdout
        assert len(fake_wp[0].created("posts")) == 1

    def test_filtered_out_records_are_not_checked(self, fake_wp, tmp_path):
        path = write_json_tab(tmp_path, [
            {"_row_num": 2, "action": "add", "type": "posts", "title": "A"},
            {"_row_num": 3, "action": "update", "type": "pages", "title": "Без id"},
        ])

        result = run_sync("--file", str(path), "--sheets", CONTENT_TAB, "--filter", "type=posts")

        assert result.exit_code == 0, result.stdout

    def test_excel_typo_blocks_sync(self, fake_wp, tmp_path):
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "tilte", "slug"])
        ws.append(["", "add", "Первая", "first"])
        path = tmp_path / "export.xlsx"
        wb.save(path)

        result = run_sync("--file", str(path))

        assert result.exit_code == 2, result.stdout
        message = stats_of(result)["error"]["message"]
        assert "Лист 'posts', строка 2" in message and "'title'" in message
        assert fake_wp == []


class TestValidateExcel:
    def test_typo_found_without_credentials(self, monkeypatch, tmp_path):
        from src.cli import tool_commands

        def _no_credentials(**kwargs):
            raise AssertionError("до credentials дойти не должно")

        monkeypatch.setattr(tool_commands, "load_and_print_credentials", _no_credentials)
        wb = Workbook()
        ws = wb.active
        ws.title = "posts"
        ws.append(["id", "action", "title", "Rb_Services_Cat"])
        ws.append(["", "add", "Первая", "X"])
        path = tmp_path / "export.xlsx"
        wb.save(path)

        result = runner.invoke(app, ["--json", "validate", "--file", str(path)])

        assert result.exit_code == 2, result.stdout
        assert "rb_services_cat" in stats_of(result)["error"]["message"]
