"""
Поддержка проектов: --project <path|name> резолвит ProjectContext.

Проект (данные конкретного WP-сайта: .env, Excel/JSON, media/, content/, logs/)
живёт вне этого инструмента — CLI лишь находит его директорию. См. "Система
проектов" в AGENTS.md за полным порядком разрешения.
"""

from importlib import metadata
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass

from dotenv import dotenv_values

from ..exceptions import CliTooOld, ValidationError
from ..records.json_source import is_data_file

# Имя пакета и команды — для importlib.metadata и подсказки об обновлении.
PACKAGE_NAME = "wp-bulk-editor"

# Ключ в .env проекта: минимальная версия CLI, понимающая этот проект.
MIN_VERSION_KEY = "WP_BULK_MIN_VERSION"

# cwd-walk (см. _discover_project_by_cwd_walk): не подниматься выше этого числа
# уровней даже если домашняя директория не достигнута — защита от случайного
# .env где-нибудь совсем не по теме (например, смонтированная сетевая ФС).
_CWD_WALK_MAX_DEPTH = 15

# Расширения файлов данных проекта: источник записей выбирается по ним.
DATA_SUFFIXES = (".xlsx", ".json")


def find_repo_root() -> Path:
    """
    Найти корень репозитория wp-bulk-editor (где лежит его pyproject.toml).

    Это НЕ про расположение проекта пользователя (--project) — только про то,
    где на диске лежит сам этот инструмент. Используется исключительно для
    legacy-фолбэка (см. resolve_project) и для templates/ в tool_commands.py.
    """
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

    @property
    def min_version(self) -> Optional[str]:
        """
        Минимальная версия CLI, объявленная проектом (`WP_BULK_MIN_VERSION` в .env).

        .env проекта — единственная конфигурационная поверхность проекта здесь,
        отдельного config-файла нет. Читаем файл напрямую (`dotenv_values`),
        не трогая окружение процесса.
        """
        env_file = self.env_file
        if env_file is None:
            return None
        value = (dotenv_values(env_file).get(MIN_VERSION_KEY) or "").strip()
        return value or None

    def find_excel(self) -> Optional[Path]:
        """Найти xlsx файл в папке проекта (тот же отбор, что и у find_data_file)."""
        return self._pick(self._candidates(".xlsx"))

    def _candidates(self, suffix: str) -> list:
        """
        Файлы проекта с данным расширением, годные в источники данных.

        Отсекаются `~$*.xlsx` — временные файлы самого Excel, а не данные
        проекта. Для json дополнительно проверяется форма (`tabs`): посторонний
        json в папке (например, service-account Google) не должен ни
        выбираться как данные, ни создавать мнимую неоднозначность форматов.
        """
        if not self.root:
            return []
        files = sorted(
            p for p in self.root.glob(f"*{suffix}") if not p.name.startswith("~$")
        )
        if suffix == ".json":
            files = [p for p in files if is_data_file(p)]
        return files

    def _pick(self, files: list) -> Optional[Path]:
        """Из кандидатов одного формата: единственный, иначе названный по имени проекта."""
        if not files:
            return None
        if len(files) == 1:
            return files[0]
        for f in files:
            if f.stem == self.name:
                return f
        return files[0]

    def find_data_file(self) -> Optional[Path]:
        """
        Найти файл данных проекта: xlsx или json.

        Источник записей выбирается по расширению, поэтому двусмысленность
        разруливать молча нельзя: если в папке лежат файлы обоих форматов —
        ошибка со списком кандидатов, нужен явный `--file`. Внутри одного
        формата приоритет у файла, названного по имени проекта.

        Raises:
            ValidationError: в папке проекта есть и xlsx, и json (exit 2).
        """
        if not self.root:
            return None

        by_suffix = {suffix: self._candidates(suffix) for suffix in DATA_SUFFIXES}
        present = [suffix for suffix, files in by_suffix.items() if files]

        if not present:
            return None

        if len(present) > 1:
            names = ", ".join(f.name for suffix in present for f in by_suffix[suffix])
            raise ValidationError(
                f"В папке проекта {self.root} есть файлы данных разных форматов: {names}. "
                "Формат источника определяется по расширению — укажите нужный файл явно "
                "через --file."
            )

        return self._pick(by_suffix[present[0]])

    def resolve_output(self, default_name: str) -> Path:
        """Разрешить путь для export-команд."""
        if self.root:
            return self.root / default_name
        return Path(default_name)


def _discover_project_by_cwd_walk() -> Optional[Path]:
    """
    cwd-walk: найти корень проекта, поднимаясь от текущей директории вверх.

    Маркер "это директория проекта" — лежащий прямо в ней `.env` (тот же
    признак, что использует ProjectContext.env_file/min_version). Проверяем
    саму директорию, затем каждого родителя, пока не найдём `.env`, не
    упрёмся в корень ФС, не дойдём до домашней директории пользователя или не
    превысим _CWD_WALK_MAX_DEPTH — что наступит раньше. Ограничение на
    домашнюю директорию и глубину — чтобы случайный `.env` где-то совсем не
    по теме (выше `~` или на большой глубине) не давал неожиданное совпадение.
    """
    home = Path.home().resolve()
    current = Path.cwd().resolve()

    for _ in range(_CWD_WALK_MAX_DEPTH):
        if (current / ".env").exists():
            return current
        if current == current.parent or current == home:
            return None
        current = current.parent

    return None


def resolve_project(project_value: Optional[str]) -> ProjectContext:
    """
    Разрешить --project/-P (или WP_PROJECT) в ProjectContext.

    Порядок разрешения:
    1. Значение указывает на существующую директорию на диске (абсолютный
       путь или путь относительно cwd) — используется как есть, `name` для
       отображения — basename директории. Явный путь побеждает безусловно,
       даже если ниже могло бы совпасть legacy-имя.
    2. Иначе — legacy-фолбэк: `<repo_root>/projects/<value>/` внутри
       репозитория wp-bulk-editor (для проектов, всё ещё физически там
       лежащих).
    3. Значение не передано вовсе — cwd-walk (см. _discover_project_by_cwd_walk).
    4. Ничего не найдено — неактивный ProjectContext() (глобальный режим).
    """
    if not project_value:
        discovered = _discover_project_by_cwd_walk()
        if discovered is not None:
            return ProjectContext(name=discovered.name, root=discovered)
        return ProjectContext()

    explicit_path = Path(project_value).expanduser()
    if not explicit_path.is_absolute():
        explicit_path = Path.cwd() / explicit_path
    if explicit_path.is_dir():
        return ProjectContext(name=explicit_path.name, root=explicit_path.resolve())

    repo_root = find_repo_root()
    project_dir = repo_root / "projects" / project_value

    if not project_dir.exists():
        raise FileNotFoundError(
            f"Папка проекта не найдена: {project_dir}\n"
            f"Создайте: mkdir projects/{project_value}"
        )

    return ProjectContext(name=project_value, root=project_dir.resolve())


def installed_version() -> Optional[str]:
    """
    Версия установленного пакета или None при запуске из исходников.

    Голый запуск из репозитория (пакет не установлен) версии не имеет —
    проверку совместимости в этом случае пропускаем, а не проваливаем.
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None


def _version_key(value: str) -> Tuple[int, ...]:
    """Версия → кортеж чисел для сравнения. Нечисловые хвосты (1.2.0rc1) отбрасываются."""
    parts = []
    for chunk in value.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_cli_compat(project: ProjectContext, version: Optional[str] = None) -> None:
    """
    Проверить, что установленный CLI не старее объявленного проектом минимума.

    Raises:
        CliTooOld: установленная версия младше `WP_BULK_MIN_VERSION` (exit 6).
    """
    required = project.min_version
    if not required:
        return

    current = version if version is not None else installed_version()
    if current is None:
        return

    if _version_key(current) < _version_key(required):
        raise CliTooOld(
            f"Проект '{project.name}' требует wp-bulk-editor >= {required}, "
            f"установлена версия {current}."
        )
