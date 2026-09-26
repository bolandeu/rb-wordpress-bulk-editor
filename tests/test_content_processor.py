"""
Tests for src/utils/content_processor.py — content_path → блоки Gutenberg.
"""

from src.utils.content_processor import ContentProcessor


class TestProcessFile:
    def test_unknown_tags_are_kept_not_dropped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.html").write_text(
            '<div><h2>Заголовок</h2><p>Текст</p><iframe src="v"></iframe></div>', encoding="utf-8"
        )

        blocks = ContentProcessor(tmp_path).process_file("a.html")

        assert "<!-- wp:heading" in blocks
        assert "<!-- wp:paragraph -->" in blocks
        assert '<!-- wp:html -->\n<iframe src="v"></iframe>' in blocks

    def test_markdown_is_converted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.md").write_text("# Заголовок", encoding="utf-8")

        assert '<!-- wp:heading {"level":1} -->' in ContentProcessor(tmp_path).process_file("a.md")
