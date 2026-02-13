"""
Поддержка проектов: --project <name> загружает конфиг из projects/<name>/
"""

from pathlib import Path
from typing import Optional
from dataclasses import dataclass


def find_repo_root() -> Path:
    """Найти корень репозитория (где лежит pyproject.toml)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()


@dataclass
class ProjectContext:
    """Контекст проекта. Хранится в typer.Context.obj."""
    name: Optional[str] = None
    root: Optional[Path] = None

    @property
    def active(self) -> bool:
        return self.name is not None and self.root is not None

    @property
    def env_file(self) -> Optional[Path]:
        if self.root:
            p = self.root / ".env"
            return p if p.exists() else None
        return None

    @property
    def logs_folder(self) -> Path:
        if self.root:
            return self.root / "logs"
        return Path.cwd() / "logs"

    @property
    def media_folder(self) -> Path:
        if self.root:
            return self.root / "media"
        return Path("data/media")

    @property
    def content_folder(self) -> Path:
        if self.root:
            return self.root / "content"
        return Path("data/content")

    def find_excel(self) -> Optional[Path]:
        """Найти xlsx файл в папке проекта."""
        if not self.root:
            return None
        xlsx_files = sorted(self.root.glob("*.xlsx"))
        if not xlsx_files:
            return None
        # Если один файл — он и есть
        if len(xlsx_files) == 1:
            return xlsx_files[0]
        # Если есть файл с именем проекта — приоритет
        for f in xlsx_files:
            if f.stem == self.name:
                return f
        return xlsx_files[0]

    def resolve_output(self, default_name: str) -> Path:
        """Разрешить путь для export-команд."""
        if self.root:
            return self.root / default_name
        return Path(default_name)


def resolve_project(project_name: Optional[str]) -> ProjectContext:
    """Разрешить имя проекта в ProjectContext."""
    if not project_name:
        return ProjectContext()

    repo_root = find_repo_root()
    project_dir = repo_root / "projects" / project_name

    if not project_dir.exists():
        raise FileNotFoundError(
            f"Папка проекта не найдена: {project_dir}\n"
            f"Создайте: mkdir projects/{project_name}"
        )

    return ProjectContext(name=project_name, root=project_dir.resolve())
