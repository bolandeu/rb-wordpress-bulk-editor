"""
Tests for src/config.py Pydantic models.
"""

import pytest
from pathlib import Path

from src.config import WordPressConfig, SyncConfig, LoggingConfig, PathsConfig


# ── WordPressConfig ─────────────────────────────────────────────────

class TestWordPressConfig:
    def test_url_strip_and_trailing_slash(self):
        cfg = WordPressConfig(url="  https://example.com/  ")
        assert cfg.url == "https://example.com"

    def test_url_adds_https(self):
        cfg = WordPressConfig(url="example.com")
        assert cfg.url == "https://example.com"

    def test_url_preserves_http(self):
        cfg = WordPressConfig(url="http://localhost")
        assert cfg.url == "http://localhost"

    def test_url_removes_trailing_slash(self):
        cfg = WordPressConfig(url="https://site.com/")
        assert cfg.url == "https://site.com"

    def test_password_space_removal(self):
        cfg = WordPressConfig(url="https://x.com", password="AB CD EF 12")
        assert cfg.password == "ABCDEF12"

    def test_password_none(self):
        cfg = WordPressConfig(url="https://x.com", password=None)
        assert cfg.password is None

    def test_verify_ssl_default(self):
        cfg = WordPressConfig(url="https://x.com")
        assert cfg.verify_ssl is True


# ── SyncConfig ──────────────────────────────────────────────────────

class TestSyncConfig:
    def test_defaults(self):
        cfg = SyncConfig()
        assert cfg.batch_size == 10
        assert cfg.max_retries == 3
        assert cfg.timeout == 30
        assert cfg.concurrency == 5
        assert cfg.dry_run is False
        assert cfg.skip_media is False
        assert cfg.update_excel is True

    def test_boundary_batch_size(self):
        assert SyncConfig(batch_size=1).batch_size == 1
        assert SyncConfig(batch_size=100).batch_size == 100

    def test_invalid_batch_size(self):
        with pytest.raises(Exception):
            SyncConfig(batch_size=0)
        with pytest.raises(Exception):
            SyncConfig(batch_size=101)

    def test_invalid_concurrency(self):
        with pytest.raises(Exception):
            SyncConfig(concurrency=0)
        with pytest.raises(Exception):
            SyncConfig(concurrency=21)


# ── LoggingConfig ───────────────────────────────────────────────────

class TestLoggingConfig:
    def test_defaults(self):
        cfg = LoggingConfig()
        assert cfg.level == "INFO"
        assert cfg.rotation == "10 MB"
        assert cfg.retention == "30 days"

    def test_custom_level(self):
        cfg = LoggingConfig(level="DEBUG")
        assert cfg.level == "DEBUG"

    def test_invalid_level(self):
        with pytest.raises(Exception):
            LoggingConfig(level="TRACE")


# ── PathsConfig ─────────────────────────────────────────────────────

class TestPathsConfig:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(Exception, match="не найден"):
            PathsConfig(excel_file=tmp_path / "nonexistent.xlsx")

    def test_wrong_extension_raises(self, tmp_path):
        csv_file = tmp_path / "data.csv"
        csv_file.write_bytes(b"")
        with pytest.raises(Exception, match="формат"):
            PathsConfig(excel_file=csv_file)

    def test_valid_xlsx(self, tmp_excel_file):
        cfg = PathsConfig(excel_file=tmp_excel_file)
        assert cfg.excel_file == tmp_excel_file

    def test_default_folders(self, tmp_excel_file):
        cfg = PathsConfig(excel_file=tmp_excel_file)
        assert cfg.media_folder == Path("data/media")
        assert cfg.logs_folder == Path("logs")
