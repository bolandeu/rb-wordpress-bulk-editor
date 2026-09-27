"""
Tests for ProjectContext.find_data_file()/find_excel() — выбор файла данных.

Источник записей определяется расширением, поэтому xlsx и json рядом — это
не «выбери любой», а требование указать --file явно. Посторонние json в папке
(credentials и прочее) кандидатами не считаются.
"""

import json

import pytest

from src.cli.project import ProjectContext
from src.exceptions import ValidationError


def make_project(root, name="myproj") -> ProjectContext:
    return ProjectContext(name=name, root=root)


def write_data_json(path, tabs=None):
    """Файл нужной формы: объект с `tabs`."""
    path.write_text(
        json.dumps({"project": "x", "tabs": tabs or {"site.ru": []}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def write_foreign_json(path):
    """Посторонний json: валидный, но не файл данных (service-account Google)."""
    path.write_text(json.dumps({"type": "service_account", "private_key": "…"}), encoding="utf-8")
    return path


class TestFindDataFile:
    def test_none_without_root(self):
        assert ProjectContext().find_data_file() is None

    def test_empty_project(self, tmp_path):
        assert make_project(tmp_path).find_data_file() is None

    def test_single_xlsx(self, tmp_path):
        (tmp_path / "export.xlsx").write_bytes(b"")

        assert make_project(tmp_path).find_data_file().name == "export.xlsx"

    def test_single_json(self, tmp_path):
        write_data_json(tmp_path / "mw.json")

        assert make_project(tmp_path).find_data_file().name == "mw.json"

    def test_project_named_file_wins_within_one_format(self, tmp_path):
        write_data_json(tmp_path / "aaa.json")
        write_data_json(tmp_path / "myproj.json")

        assert make_project(tmp_path).find_data_file().name == "myproj.json"

    def test_foreign_json_is_not_a_candidate(self, tmp_path):
        """Чужой json рядом с xlsx не должен делать выбор неоднозначным."""
        (tmp_path / "export.xlsx").write_bytes(b"")
        write_foreign_json(tmp_path / "google-credentials.json")

        assert make_project(tmp_path).find_data_file().name == "export.xlsx"

    def test_foreign_json_does_not_win_alphabetically(self, tmp_path):
        """Из двух json данные — только тот, у которого есть `tabs`."""
        write_foreign_json(tmp_path / "aaa-credentials.json")
        write_data_json(tmp_path / "site.json")

        assert make_project(tmp_path).find_data_file().name == "site.json"

    def test_broken_json_is_not_a_candidate(self, tmp_path):
        (tmp_path / "export.xlsx").write_bytes(b"")
        (tmp_path / "broken.json").write_text("{не json", encoding="utf-8")

        assert make_project(tmp_path).find_data_file().name == "export.xlsx"

    def test_both_formats_require_explicit_file(self, tmp_path):
        (tmp_path / "myproj.xlsx").write_bytes(b"")
        write_data_json(tmp_path / "myproj.json")

        with pytest.raises(ValidationError) as exc:
            make_project(tmp_path).find_data_file()

        assert exc.value.exit_code == 2
        assert "myproj.xlsx" in exc.value.message
        assert "myproj.json" in exc.value.message
        assert "--file" in exc.value.message

    def test_excel_lock_files_are_ignored(self, tmp_path):
        """`~$export.xlsx` — временный файл Excel, а не данные проекта."""
        (tmp_path / "export.xlsx").write_bytes(b"")
        (tmp_path / "~$export.xlsx").write_bytes(b"")

        assert make_project(tmp_path).find_data_file().name == "export.xlsx"

    def test_find_excel_still_excel_only(self, tmp_path):
        """find_excel() остаётся Excel-специфичным: json его не интересует."""
        write_data_json(tmp_path / "mw.json")

        assert make_project(tmp_path).find_excel() is None

    def test_find_excel_ignores_lock_files_too(self, tmp_path):
        """Отбор кандидатов общий: `~$*` не должен всплывать и в Excel-командах."""
        (tmp_path / "export.xlsx").write_bytes(b"")
        (tmp_path / "~$export.xlsx").write_bytes(b"")

        assert make_project(tmp_path).find_excel().name == "export.xlsx"

    def test_find_excel_lock_file_alone_is_not_data(self, tmp_path):
        (tmp_path / "~$export.xlsx").write_bytes(b"")

        assert make_project(tmp_path).find_excel() is None
        assert make_project(tmp_path).find_data_file() is None
