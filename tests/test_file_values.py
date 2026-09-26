"""
Tests for src/utils/file_values.py — значение поля как ссылка на файл.
"""

import pytest

from src.exceptions import ValidationError
from src.utils.file_values import FileValueResolver, looks_like_file_ref


class TestLooksLikeFileRef:
    @pytest.mark.parametrize("value", [
        "content-write/3425.html", "a.htm", "notes.md", "x.TXT", "/abs/path/1.html",
    ])
    def test_file_refs(self, value):
        assert looks_like_file_ref(value)

    @pytest.mark.parametrize("value", [
        "example.com", "v1.2", "обычный текст", "https://site.ru/page.html",
        "<p>a</p>\n<p>b.html</p>", "", None, 42,
    ])
    def test_not_file_refs(self, value):
        assert not looks_like_file_ref(value)


class TestFileValueResolver:
    def test_relative_path_from_base_dir(self, tmp_path):
        (tmp_path / "c").mkdir()
        (tmp_path / "c" / "1.html").write_text("  <p>x</p>\n", encoding="utf-8")

        assert FileValueResolver(tmp_path).resolve("acf.more", "c/1.html") == "<p>x</p>"

    def test_absolute_path_as_is(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        (other / "1.txt").write_text("текст", encoding="utf-8")
        resolver = FileValueResolver(tmp_path / "base")

        assert resolver.resolve("acf.more", str(other / "1.txt")) == "текст"

    def test_markdown_is_converted_to_html(self, tmp_path):
        (tmp_path / "1.md").write_text("# Заголовок", encoding="utf-8")

        assert FileValueResolver(tmp_path).resolve("content", "1.md") == "<h1>Заголовок</h1>"

    def test_non_ref_value_passes_through(self, tmp_path):
        assert FileValueResolver(tmp_path).resolve("acf.f1", "портьеры") == "портьеры"

    def test_identity_fields_are_not_read(self, tmp_path):
        (tmp_path / "page.html").write_text("не должно прочитаться", encoding="utf-8")

        assert FileValueResolver(tmp_path).resolve("slug", "page.html") == "page.html"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValidationError, match="файл не найден"):
            FileValueResolver(tmp_path).resolve("acf.more", "nope.html")

    def test_traversal_outside_base_dir_is_rejected(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        with pytest.raises(ValidationError, match="выходит за папку"):
            FileValueResolver(base).resolve("acf.more", "../secret.txt")
