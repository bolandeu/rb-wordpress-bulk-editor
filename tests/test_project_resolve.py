"""
Tests for src/cli/project.py — resolve_project(): путь / legacy-имя / cwd-walk.
"""

from pathlib import Path

import pytest

from src.cli.project import resolve_project


class TestResolveExplicitPath:
    def test_absolute_path_outside_repo(self, tmp_path):
        """Явный путь к произвольной директории на диске резолвится как есть."""
        project_dir = tmp_path / "external-project"
        project_dir.mkdir()
        (project_dir / ".env").write_text("WP_DOMAIN=https://x.com\nWP_BULK_MIN_VERSION=1.5.0\n", encoding="utf-8")

        ctx = resolve_project(str(project_dir))

        assert ctx.active
        assert ctx.root == project_dir.resolve()
        assert ctx.name == project_dir.name
        # min_version читает ctx.env_file — должен работать и для path-резолва.
        assert ctx.min_version == "1.5.0"

    def test_relative_path_resolved_against_cwd(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "rel-project"
        project_dir.mkdir()
        (project_dir / ".env").write_text("WP_DOMAIN=https://x.com\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        ctx = resolve_project("rel-project")

        assert ctx.active
        assert ctx.root == project_dir.resolve()

    def test_explicit_path_wins_even_if_legacy_name_would_match(self, tmp_path, monkeypatch):
        """Явный путь безусловно побеждает — двусмысленность не разруливается."""
        project_dir = tmp_path / "tzm"
        project_dir.mkdir()
        (project_dir / ".env").write_text("WP_DOMAIN=https://tmp.example\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        # "tzm" совпадает и с реальной projects/tzm внутри репозитория, и с
        # директорией tmp_path/tzm — должна победить директория tmp_path/tzm.
        ctx = resolve_project("tzm")

        assert ctx.root == project_dir.resolve()


class TestResolveLegacyName:
    def test_bare_name_resolves_inside_repo_projects(self):
        """Имя, не совпадающее ни с одним путём на диске, — legacy-фолбэк в projects/<name>."""
        ctx = resolve_project("tzm")

        assert ctx.active
        assert ctx.name == "tzm"
        assert ctx.root.name == "tzm"
        assert (ctx.root / "tzm.xlsx").exists()

    def test_unknown_name_raises(self):
        with pytest.raises(FileNotFoundError):
            resolve_project("no-such-project-xyz")


class TestResolveCwdWalk:
    def test_finds_env_in_parent_directory(self, tmp_path, monkeypatch):
        project_root = tmp_path / "myproj"
        project_root.mkdir()
        (project_root / ".env").write_text("WP_DOMAIN=https://x.com\nWP_BULK_MIN_VERSION=2.3.0\n", encoding="utf-8")
        nested = project_root / "sub" / "sub2"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        ctx = resolve_project(None)

        assert ctx.active
        assert ctx.root == project_root.resolve()
        assert ctx.name == "myproj"
        # min_version читает ctx.env_file — должен работать и для cwd-walk.
        assert ctx.min_version == "2.3.0"

    def test_finds_env_in_cwd_itself(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("WP_DOMAIN=https://x.com\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        ctx = resolve_project(None)

        assert ctx.active
        assert ctx.root == tmp_path.resolve()

    def test_no_env_up_to_fake_home_stays_inactive(self, tmp_path, monkeypatch):
        """
        Без .env нигде по дереву — неактивный ProjectContext.

        Домашняя директория подменена на поддиректорию tmp_path, чтобы walk не
        мог случайно уйти в реальные каталоги машины (и наткнуться там на
        чей-то .env) — он гарантированно упирается в границу внутри фикстуры.
        """
        fake_home = tmp_path / "home"
        nested = fake_home / "workspace" / "empty"
        nested.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.chdir(nested)

        ctx = resolve_project(None)

        assert not ctx.active
        assert ctx.root is None
