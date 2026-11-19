"""
Утилиты для работы с путями к файлам
"""

from pathlib import Path

from ..exceptions import ValidationError


def resolve_media_file(media_path: str, media_folder) -> Path:
    """
    Разрешить путь к медиа-файлу: абсолютный → CWD-relative → media_folder fallback.

    Защита от path traversal: относительные пути не могут выходить за пределы
    media_folder или рабочей директории.

    Args:
        media_path: Путь из Excel (строка)
        media_folder: Папка медиа (Path)

    Returns:
        Path к файлу (может не существовать)

    Raises:
        ValidationError: Если путь пытается выйти за пределы разрешённых директорий
    """
    # Запрещаем откровенно опасные паттерны
    if ".." in Path(media_path).parts:
        raise ValidationError(f"Недопустимый путь к медиафайлу (содержит '..'): {media_path}")

    file_path = Path(media_path)
    if file_path.is_absolute():
        return file_path

    # Относительно CWD
    if file_path.exists():
        resolved = file_path.resolve()
        cwd = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError:
            raise ValidationError(f"Путь выходит за пределы рабочей директории: {media_path}")
        return file_path

    # Fallback: относительно media_folder
    media_folder_path = Path(media_folder).resolve()
    fallback = media_folder_path / media_path
    resolved_fallback = fallback.resolve()
    try:
        resolved_fallback.relative_to(media_folder_path)
    except ValueError:
        raise ValidationError(f"Путь выходит за пределы папки медиафайлов: {media_path}")

    if fallback.exists():
        return fallback

    return file_path
