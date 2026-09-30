"""
Tests for src/records/json_source.py (JsonRecordSource / JsonRecordWriter)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.exceptions import RecordSourceError
from src.mappers.content_mapper import ContentMapper
from src.records.json_source import JsonRecordSource, JsonRecordWriter
from src.sync.taxonomy_sync import TaxonomySync


TAB = "example.ru"


def _document() -> dict:
    """Синтетический документ в формате antagor (project/tabs/записи)."""
    return {
        "project": "demo",
        "sheet_id": "abc123",
        "tabs": {
            "options": [
                {"_row_num": 2, "key": "phone", "label": "Телефон", "value": "+7 000"},
            ],
            TAB: [
                {
                    "_row_num": 2,
                    "action": "add",
                    "id": "",
                    "type": "rb_services",
                    "taxonomy": "",
                    "title": "Портьеры",
                    "name": "",
                    "slug": "portery",
                    "rb_services_cat": "Импорт товаров из Китая",
                    "acf.f1": "портьеры",
                    "seo.title": "Портьеры — SEO",
                    "status": "publish",
                    "link": "",
                    "modified": "",
                },
                {
                    "_row_num": 3,
                    "action": "update",
                    "id": "7",
                    "type": "",
                    "taxonomy": "rb_services_cat",
                    "title": "",
                    "name": "Импорт товаров из Китая",
                    "slug": "import-iz-kitaya",
                    "link": "",
                    "modified": "",
                },
            ],
        },
    }


def _write_json(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def json_file(tmp_path) -> Path:
    return _write_json(tmp_path / "demo.json", _document())


# ── JsonRecordSource ──────────────────────────────────────────────────


class TestJsonRecordSource:
    def test_content_record(self, json_file):
        records = list(JsonRecordSource(json_file, TAB).read())

        content = records[0]
        assert content.discriminator == "rb_services"
        assert content.is_taxonomy is False
        assert content.write_key == 2
        assert content.data["title"] == "Портьеры"
        # Метаданные источника не должны попадать в поля записи
        assert "_row_num" not in content.data
        assert "type" not in content.data
        assert "taxonomy" not in content.data

    def test_taxonomy_record(self, json_file):
        records = list(JsonRecordSource(json_file, TAB).read())

        term = records[1]
        assert term.discriminator == "rb_services_cat"
        assert term.is_taxonomy is True
        assert term.write_key == 3
        assert term.data["name"] == "Импорт товаров из Китая"
        assert "taxonomy" not in term.data
        assert "type" not in term.data

    def test_taxonomy_payload_has_no_source_metadata(self, json_file):
        """`type`/`taxonomy` не должны уезжать в payload термина (read-only в WP)."""
        term = list(JsonRecordSource(json_file, TAB).read())[1]

        payload = TaxonomySync(MagicMock(), MagicMock())._build_payload(term.data)

        assert payload["name"] == "Импорт товаров из Китая"
        assert payload["slug"] == "import-iz-kitaya"
        assert "taxonomy" not in payload
        assert "type" not in payload
        assert "_row_num" not in payload

    def test_fields(self, json_file):
        fields = JsonRecordSource(json_file, TAB).fields()

        assert "_row_num" not in fields
        assert "type" not in fields
        assert "taxonomy" not in fields
        assert fields[:2] == ["action", "id"]
        assert "acf.f1" in fields

    def test_dot_notation_fields_map_to_nested_payload(self, json_file):
        """Dot-нотация из Excel работает и для ключей JSON-записи."""
        source = JsonRecordSource(json_file, TAB)
        record = next(iter(source.read()))

        mapper = ContentMapper(source.fields())
        payload = mapper.map_row(record.data)

        assert payload["acf"] == {"f1": "портьеры"}
        assert payload["seo"] == {"title": "Портьеры — SEO"}

    def test_write_key_falls_back_to_position(self, tmp_path):
        document = _document()
        for record in document["tabs"][TAB]:
            del record["_row_num"]
        path = _write_json(tmp_path / "no_row_num.json", document)

        records = list(JsonRecordSource(path, TAB).read())
        assert [r.write_key for r in records] == [0, 1]

    def test_both_discriminators_filled_raises(self, tmp_path):
        document = _document()
        document["tabs"][TAB][0]["taxonomy"] = "rb_services_cat"
        path = _write_json(tmp_path / "both.json", document)

        with pytest.raises(RecordSourceError, match="одновременно"):
            list(JsonRecordSource(path, TAB).read())

    def test_no_discriminator_raises(self, tmp_path):
        document = _document()
        document["tabs"][TAB][0]["type"] = ""
        path = _write_json(tmp_path / "neither.json", document)

        with pytest.raises(RecordSourceError, match="не заполнено"):
            list(JsonRecordSource(path, TAB).read())

    def test_duplicate_write_key_raises(self, tmp_path):
        document = _document()
        document["tabs"][TAB][1]["_row_num"] = 2
        path = _write_json(tmp_path / "dup.json", document)

        with pytest.raises(RecordSourceError, match="уже занят"):
            list(JsonRecordSource(path, TAB).read())

    def test_validation_happens_before_first_record(self, tmp_path):
        """Битая запись в конце таба должна отвалиться ДО синхронизации первой."""
        document = _document()
        document["tabs"][TAB][1]["type"] = "page"  # и type, и taxonomy
        path = _write_json(tmp_path / "late_error.json", document)

        source = JsonRecordSource(path, TAB)
        with pytest.raises(RecordSourceError, match="одновременно"):
            source.read()

    def test_unknown_tab_raises(self, json_file):
        with pytest.raises(RecordSourceError, match="не найден"):
            JsonRecordSource(json_file, "missing.ru").fields()

    def test_broken_json_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(RecordSourceError, match="Некорректный JSON"):
            JsonRecordSource(path, TAB).fields()


# ── JsonRecordWriter ──────────────────────────────────────────────────


class TestJsonRecordWriter:
    def test_round_trip_touches_only_target_record(self, json_file):
        before = json.loads(json_file.read_text(encoding="utf-8"))

        writer = JsonRecordWriter(json_file, TAB)
        writer.load()
        writer.update_from_result(
            2,
            {"id": 55, "link": "https://example.ru/portery", "modified": "2026-01-01"},
            "add",
        )
        writer.save()

        after = json.loads(json_file.read_text(encoding="utf-8"))

        updated = after["tabs"][TAB][0]
        assert updated["id"] == 55
        assert updated["link"] == "https://example.ru/portery"
        assert updated["modified"] == "2026-01-01"
        # После успешной операции action → skip
        assert updated["action"] == "skip"
        # Остальные поля записи не тронуты
        assert updated["title"] == before["tabs"][TAB][0]["title"]
        assert updated["_row_num"] == 2

        # Соседняя запись и другие табы — без изменений
        assert after["tabs"][TAB][1] == before["tabs"][TAB][1]
        assert after["tabs"]["options"] == before["tabs"]["options"]
        assert after["project"] == before["project"]
        assert after["sheet_id"] == before["sheet_id"]

    def test_id_written_even_if_key_absent(self, tmp_path):
        """Запись без ключа `id` не должна терять ID созданного поста."""
        document = _document()
        del document["tabs"][TAB][0]["id"]
        path = _write_json(tmp_path / "no_id.json", document)

        writer = JsonRecordWriter(path, TAB)
        writer.load()
        writer.update_from_result(2, {"id": 55, "link": "https://example.ru/portery"}, "add")
        writer.save()

        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["tabs"][TAB][0]["id"] == 55
        assert after["tabs"][TAB][0]["link"] == "https://example.ru/portery"

    def test_content_path_kept_after_sync(self, tmp_path):
        """content_path — ссылка на файл статьи: обратная запись его не зануляет."""
        document = _document()
        document["tabs"][TAB][0]["content_path"] = "news/portery.html"
        path = _write_json(tmp_path / "content_path.json", document)

        writer = JsonRecordWriter(path, TAB)
        writer.load()
        writer.update_from_result(2, {"id": 55, "link": "https://example.ru/portery"}, "add")
        writer.save()

        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["tabs"][TAB][0]["content_path"] == "news/portery.html"

    def test_taxonomy_ids_converted_to_names(self, json_file):
        """С taxonomy_cache кастомная таксономия пишется именами, а не ID."""
        cache = MagicMock()
        cache.cache = {"rb_services_cat": {}}
        cache.get_id_to_name_mapping = MagicMock(
            return_value={"rb_services_cat": {12: "Импорт товаров из Китая", 15: "Ткани"}}
        )

        writer = JsonRecordWriter(json_file, TAB, taxonomy_cache=cache)
        writer.load()
        writer.update_from_result(2, {"id": 55, "rb_services_cat": [12, 15]}, "add")
        writer.save()

        after = json.loads(json_file.read_text(encoding="utf-8"))
        assert after["tabs"][TAB][0]["rb_services_cat"] == "Импорт товаров из Китая; Ткани"

    def test_unrelated_api_fields_not_dumped_into_record(self, json_file):
        """В файл проекта не должен утекать весь ответ WordPress."""
        writer = JsonRecordWriter(json_file, TAB)
        writer.load()
        writer.update_from_result(
            2,
            {"id": 55, "guid": {"rendered": "?p=55"}, "template": "", "_links": {"self": []}},
            "add",
        )
        writer.save()

        after = json.loads(json_file.read_text(encoding="utf-8"))["tabs"][TAB][0]
        assert after["id"] == 55
        assert "guid" not in after
        assert "template" not in after
        assert "_links" not in after

    def test_duplicate_write_key_raises_on_load(self, tmp_path):
        document = _document()
        document["tabs"][TAB][1]["_row_num"] = 2
        path = _write_json(tmp_path / "dup.json", document)

        with pytest.raises(RecordSourceError, match="встречается у записей"):
            JsonRecordWriter(path, TAB).load()

    def test_save_leaves_no_temp_file(self, json_file):
        writer = JsonRecordWriter(json_file, TAB)
        writer.load()
        writer.update_record(2, {"id": 55})
        writer.save()

        assert list(json_file.parent.glob("*.tmp")) == []

    def test_save_skipped_without_changes(self, json_file):
        original = json_file.read_bytes()

        writer = JsonRecordWriter(json_file, TAB)
        writer.load()
        writer.save()

        assert json_file.read_bytes() == original

    def test_update_from_result_async(self, json_file):
        import asyncio

        writer = JsonRecordWriter(json_file, TAB)
        writer.load()
        asyncio.run(writer.update_from_result_async(3, {"id": 7, "modified": "2026-02-02"}, "update"))
        writer.save()

        after = json.loads(json_file.read_text(encoding="utf-8"))
        assert after["tabs"][TAB][1]["modified"] == "2026-02-02"
        assert after["tabs"][TAB][1]["action"] == "skip"
