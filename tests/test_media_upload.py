"""
Tests for команды `media-upload`: файлы с диска → вложения WordPress.

Сайт подменён httpx.MockTransport: загрузка отвечает id по имени файла,
отдельные файлы/запросы метаданных можно заставить падать.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from rb_wordpress_bulk_editor.cli import app, media_commands
from rb_wordpress_bulk_editor.wordpress.client import WordPressClient

runner = CliRunner()


@pytest.fixture
def site(monkeypatch):
    """Фальшивый сайт: state — что сломать и какие запросы пришли."""
    state = {"fail_upload": set(), "fail_meta": set(), "status": 400, "requests": [], "meta": {}}
    ids = {}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path == "/wp-json/wp/v2/media":
            # как настоящий WordPress: тело разбирается по первому Content-Type
            if request.headers.get_list("content-type") != ["image/jpeg"]:
                return httpx.Response(400, json={"code": "rest_invalid_json", "message": "тело не JSON"})
            name = request.headers["Content-Disposition"].split('filename="')[1].rstrip('"')
            if name in state["fail_upload"]:
                return httpx.Response(state["status"], json={"code": "rest_upload", "message": "отказ"})
            media_id = ids.setdefault(name, 4800 + len(ids))
            return httpx.Response(201, json={"id": media_id, "source_url": f"https://example.com/up/{name}"})
        media_id = int(request.url.path.rsplit("/", 1)[1])
        if media_id in {ids.get(n) for n in state["fail_meta"]}:
            return httpx.Response(400, json={"code": "rest_invalid", "message": "meta отказ"})
        state["meta"][media_id] = json.loads(request.content)
        return httpx.Response(200, json={"id": media_id})

    monkeypatch.setattr(
        media_commands,
        "load_and_print_credentials",
        lambda **kwargs: ("https://example.com", "admin", "secret"),
    )
    monkeypatch.setattr(
        media_commands,
        "WordPressClient",
        lambda config: WordPressClient(config, transport=httpx.MockTransport(handler)),
    )
    return state


@pytest.fixture
def files(tmp_path):
    for name in ("3425.jpg", "3426.jpg"):
        (tmp_path / name).write_bytes(b"\xff\xd8fake")
    return tmp_path


def run(*args):
    result = runner.invoke(app, ["--json", "media-upload", *args])
    return result, json.loads(result.stdout.strip().splitlines()[-1])


def write_manifest(tmp_path, items):
    manifest = tmp_path / "uploads.json"
    manifest.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return str(manifest)


class TestSingleMode:
    def test_upload_sets_metadata_and_returns_path_verbatim(self, site, files):
        path = str(files / "3425.jpg")

        result, payload = run(path, "--alt", "Портьеры из Китая", "--title", "Портьеры")

        assert result.exit_code == 0
        assert payload["ok"] is True
        [entry] = payload["uploaded"]
        assert entry["path"] == path
        assert isinstance(entry["id"], int)
        assert entry["source_url"].endswith("/3425.jpg")
        assert site["meta"][entry["id"]] == {"alt_text": "Портьеры из Китая", "title": "Портьеры"}
        assert payload["failed"] == [] and payload["uploaded_count"] == 1

    def test_no_metadata_means_no_second_request(self, site, files):
        result, payload = run(str(files / "3425.jpg"))

        assert result.exit_code == 0
        assert len(site["requests"]) == 1

    def test_manifest_and_paths_are_exclusive(self, site, files):
        manifest = write_manifest(files, [{"path": str(files / "3425.jpg")}])

        result, payload = run(str(files / "3426.jpg"), "--manifest", manifest)

        assert result.exit_code == 2
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert site["requests"] == []

    def test_nothing_to_upload(self, site):
        result, payload = run()

        assert result.exit_code == 2 and payload["ok"] is False


class TestManifestMode:
    def test_partial_success_is_success(self, site, files, monkeypatch):
        missing = str(files / "3499.jpg")
        relative = "3426.jpg"  # от CWD, дословно
        monkeypatch.chdir(files)
        manifest = write_manifest(
            files,
            [
                {"path": str(files / "3425.jpg"), "alt": "a"},
                {"path": relative, "title": "t"},
                {"path": missing},
            ],
        )

        result, payload = run("--manifest", manifest)

        assert result.exit_code == 0
        assert payload["uploaded_count"] == 2 and payload["failed_count"] == 1
        assert {e["path"] for e in payload["uploaded"]} == {str(files / "3425.jpg"), relative}
        assert payload["failed"] == [
            {"path": missing, "error": f"Файл не найден: {missing}", "code": "INVALID_ARGUMENTS"}
        ]
        # строки прогресса [n/3] — в stderr (под --json лог туда)
        for n in (1, 2, 3):
            assert f"[{n}/3]" in result.stderr

    def test_site_refusing_one_file_does_not_stop_others(self, site, files):
        site["fail_upload"] = {"3426.jpg"}
        manifest = write_manifest(files, [{"path": str(files / n)} for n in ("3425.jpg", "3426.jpg")])

        result, payload = run("--manifest", manifest)

        assert result.exit_code == 0
        assert [e["path"] for e in payload["uploaded"]] == [str(files / "3425.jpg")]
        assert payload["failed"][0]["path"] == str(files / "3426.jpg")
        assert payload["failed"][0]["code"] == "PROVIDER_ERROR"
        assert "3426.jpg -> error:" in result.stderr

    def test_metadata_failure_keeps_the_attachment_id(self, site, files):
        """Вложение уже создано — его id нельзя терять, иначе повтор даст дубль."""
        site["fail_meta"] = {"3425.jpg"}

        result, payload = run(str(files / "3425.jpg"), "--alt", "a")

        assert result.exit_code == 0
        [entry] = payload["uploaded"]
        assert isinstance(entry["id"], int) and "meta_error" in entry
        assert payload["failed"] == []

    def test_all_files_missing_is_an_error_without_site(self, files, monkeypatch):
        def no_credentials(**kwargs):
            raise AssertionError("к сайту обращаться не должны")

        monkeypatch.setattr(media_commands, "load_and_print_credentials", no_credentials)
        manifest = write_manifest(files, [{"path": "/nope/1.jpg"}, {"path": "/nope/2.jpg"}])

        result, payload = run("--manifest", manifest)

        assert result.exit_code == 2
        assert payload["ok"] is False and payload["error"]["code"] == "INVALID_ARGUMENTS"

    def test_all_refused_by_site_takes_site_exit_code(self, site, files):
        site["fail_upload"] = {"3425.jpg"}
        site["status"] = 401
        manifest = write_manifest(files, [{"path": str(files / "3425.jpg")}, {"path": "/nope/x.jpg"}])

        result, payload = run("--manifest", manifest)

        assert result.exit_code == 3
        assert payload["error"]["code"] == "AUTH_REQUIRED"

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "{}",
            "[]",
            '[{"alt": "без пути"}]',
            '[{"path": "a.jpg", "atl": "опечатка"}]',
            '[{"path": "a.jpg", "alt": 5}]',
        ],
    )
    def test_bad_manifest(self, site, tmp_path, content):
        manifest = tmp_path / "m.json"
        manifest.write_text(content, encoding="utf-8")

        result, payload = run("--manifest", str(manifest))

        assert result.exit_code == 2 and payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert site["requests"] == []

    def test_meta_flags_rejected_with_manifest(self, site, files):
        manifest = write_manifest(files, [{"path": str(files / "3425.jpg")}])

        result, payload = run("--manifest", manifest, "--alt", "x")

        assert result.exit_code == 2


class TestDryRun:
    def test_checks_files_without_site(self, files, monkeypatch):
        def no_credentials(**kwargs):
            raise AssertionError("к сайту обращаться не должны")

        monkeypatch.setattr(media_commands, "load_and_print_credentials", no_credentials)
        manifest = write_manifest(files, [{"path": str(files / "3425.jpg")}, {"path": "/nope/x.jpg"}])

        result, payload = run("--manifest", manifest, "--dry-run")

        assert result.exit_code == 0
        assert payload["dry_run"] is True
        assert payload["uploaded"] == [{"path": str(files / "3425.jpg"), "id": None, "source_url": None}]
        assert payload["failed"][0]["path"] == "/nope/x.jpg"
