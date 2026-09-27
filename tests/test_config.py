"""
Tests for src/config.py Pydantic models.
"""

import pytest
from pathlib import Path

from src.config import AppConfig, WordPressConfig, SyncConfig, LoggingConfig, PathsConfig, Settings
from src.constants import DEFAULT_SKIP_POST_TYPE_PATTERNS, DEFAULT_SKIP_TAXONOMY_PATTERNS


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

    def test_from_credentials_reads_verify_ssl_from_env(self, monkeypatch):
        """`from_credentials` должен забирать WP_VERIFY_SSL из окружения (для локального Docker)."""
        monkeypatch.setenv("WP_VERIFY_SSL", "false")
        cfg = WordPressConfig.from_credentials("https://x.com", "u", "p")
        assert cfg.verify_ssl is False


# ── AppConfig ───────────────────────────────────────────────────────


class TestAppConfigFromExcel:
    def test_propagates_verify_ssl_from_env(self, monkeypatch, tmp_path):
        """Регрессия: AppConfig.from_excel должен пробрасывать WP_VERIFY_SSL в WordPressConfig."""
        monkeypatch.setenv("WP_VERIFY_SSL", "false")
        excel = tmp_path / "f.xlsx"
        excel.touch()
        cfg = AppConfig.from_excel(
            excel_file=excel,
            wp_url="https://x.com",
            wp_username="u",
            wp_password="p",
        )
        assert cfg.wordpress.verify_ssl is False


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


# ── Settings (переменные окружения WP_*) ─────────────────────────────


class TestSettingsParseCsv:
    def test_basic(self):
        assert Settings._parse_csv("a,b,c") == ["a", "b", "c"]

    def test_trims_whitespace(self):
        assert Settings._parse_csv(" a , b ,  c ") == ["a", "b", "c"]

    def test_skips_empty_items(self):
        assert Settings._parse_csv("a,,b,  ,c") == ["a", "b", "c"]

    def test_empty_input(self):
        assert Settings._parse_csv("") == []


class TestSettingsTaxonomies:
    def test_explicit_list(self):
        s = Settings(taxonomies="brand,product_cat,regions")
        assert s.get_taxonomies() == ["brand", "product_cat", "regions"]

    def test_empty_returns_none(self):
        """None = «override не задан», вызывающий код использует fallback."""
        assert Settings(taxonomies="").get_taxonomies() is None


class TestSettingsSeoFields:
    def test_explicit_list(self):
        s = Settings(seo_fields="seo.title,seo.description")
        assert s.get_seo_fields() == ["seo.title", "seo.description"]

    def test_empty_returns_none(self):
        assert Settings(seo_fields="").get_seo_fields() is None


class TestSettingsSkipPatterns:
    def test_skip_post_type_fallback_to_defaults(self):
        assert Settings(skip_post_type_patterns="").get_skip_post_type_patterns() == list(
            DEFAULT_SKIP_POST_TYPE_PATTERNS
        )

    def test_skip_taxonomy_fallback_to_defaults(self):
        assert Settings(skip_taxonomy_patterns="").get_skip_taxonomy_patterns() == list(DEFAULT_SKIP_TAXONOMY_PATTERNS)

    def test_override_wins(self):
        s = Settings(skip_post_type_patterns="custom1,custom2")
        assert s.get_skip_post_type_patterns() == ["custom1", "custom2"]
