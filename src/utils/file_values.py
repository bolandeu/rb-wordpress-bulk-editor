"""
Значение поля = путь к файлу → в payload уходит содержимое файла.

Пример: `acf.more = "content-write/3425.html"` — на сайт отправляется HTML
из файла, а не строка с путём. Работает для любого поля, которое идёт в API
(контент, ACF, dot-нотация), кроме полей-идентификаторов (`IDENTITY_FIELDS`).

Признак ссылки на файл — однострочное значение без `://`, у которого последний
сегмент пути заканчивается расширением из `FILE_VALUE_EXTENSIONS`. Список
расширений закрытый: иначе `example.com` или `v1.2` сошли бы за файл.

Пути:
- относительный — от папки файла данных (xlsx/json); выход за неё через `..`
  запрещён (path traversal);
- абсолютный — как есть: его явно написал автор файла данных.

Файл не найден — ошибка записи (`ValidationError`), а не отправка пути строкой.
"""

from pathlib import Path
from typing import Any, Optional

import markdown

from ..exceptions import ValidationError

# Расширения, по которым значение считается ссылкой на файл.
FILE_VALUE_EXTENSIONS = {".html", ".htm", ".md", ".markdown", ".txt"}
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}

# Поля, где путеподобное значение — это данные, а не ссылка на файл
# (например slug или custom_permalink вида `section/page.html`).
IDENTITY_FIELDS = {"title", "name", "slug", "custom_permalink", "link", "status", "password"}


def looks_like_file_ref(value: Any) -> bool:
    """Похоже ли значение на ссылку на файл (без проверки существования)."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "\n" in text or "://" in text or len(text) > 1024:
        return False
    return Path(text).suffix.lower() in FILE_VALUE_EXTENSIONS


class FileValueResolver:
    """Подставляет содержимое файла вместо значения-пути."""

    def __init__(self, base_dir: Optional[Path]):
        """
        Args:
            base_dir: Папка, от которой считаются относительные пути
                (папка файла данных). None — относительные пути не поддерживаются.
        """
        self.base_dir = base_dir.resolve() if base_dir else None

    def resolve(self, field: str, value: Any) -> Any:
        """Вернуть содержимое файла, если значение — ссылка на файл, иначе value."""
        if field in IDENTITY_FIELDS or not looks_like_file_ref(value):
            return value

        path = self._resolve_path(field, value.strip())
        if not path.is_file():
            raise ValidationError(f"Поле '{field}': файл не найден: {path}")

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ValidationError(f"Поле '{field}': файл не в UTF-8: {path.name} ({e})") from e

        if path.suffix.lower() in _MARKDOWN_EXTENSIONS:
            text = markdown.markdown(text, extensions=["extra", "nl2br", "sane_lists"])
        return text.strip()

    def check(self, field: str, value: Any) -> Optional[str]:
        """Проблема со ссылкой на файл без чтения самого файла (None — всё в порядке)."""
        if field in IDENTITY_FIELDS or not looks_like_file_ref(value):
            return None
        try:
            path = self._resolve_path(field, value.strip())
        except ValidationError as e:
            return str(e)
        if not path.is_file():
            return f"Поле '{field}': файл не найден: {path}"
        return None

    def _resolve_path(self, field: str, raw: str) -> Path:
        """Абсолютный путь — как есть; относительный — внутри base_dir."""
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path.resolve()

        if self.base_dir is None:
            raise ValidationError(
                f"Поле '{field}': относительный путь '{raw}' — неизвестна папка файла данных"
            )
        resolved = (self.base_dir / path).resolve()
        try:
            resolved.relative_to(self.base_dir)
        except ValueError:
            raise ValidationError(
                f"Поле '{field}': путь '{raw}' выходит за папку файла данных"
            ) from None
        return resolved
