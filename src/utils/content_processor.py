"""
Обработка контента из markdown/html файлов
"""

import logging
from pathlib import Path
from typing import Optional
import markdown
from html_to_gutenberg import HtmlToGutenberg
from loguru import logger

# Библиотека дублирует свои предупреждения в stdlib logging; без обработчика
# они вылезли бы в stderr вторым экземпляром. Пишем их сами через loguru.
logging.getLogger("html_to_gutenberg").addHandler(logging.NullHandler())


class ContentProcessor:
    """Обработка контента из файлов и конвертация в Gutenberg блоки."""

    def __init__(self, content_folder: Path):
        """
        Args:
            content_folder: Папка с файлами контента (обычно data/content/, используется как fallback)
        """
        self.content_folder = content_folder
        self.gutenberg_converter = HtmlToGutenberg()

    def _resolve_file_path(self, filename: str) -> Optional[Path]:
        """
        Разрешить путь к файлу (сначала от корня проекта, затем в content_folder).
        Запрещает абсолютные пути и path traversal за пределы content_folder.

        Args:
            filename: Имя файла или относительный путь

        Returns:
            Path к файлу или None если не найден
        """
        # Запрещаем абсолютные пути
        if Path(filename).is_absolute():
            logger.warning(f"Absolute paths not allowed: {filename}")
            return None

        # Нормализуем и проверяем на path traversal
        safe_name = Path(filename).as_posix()
        if ".." in safe_name.split("/"):
            logger.warning(f"Path traversal detected: {filename}")
            return None

        # Сначала проверяем относительно CWD
        file_path = Path(filename)
        if file_path.exists():
            return file_path.resolve()

        # Затем в content_folder как fallback
        fallback_path = self.content_folder / filename
        if fallback_path.exists():
            resolved = fallback_path.resolve()
            # Проверяем что resolved path внутри content_folder
            try:
                resolved.relative_to(self.content_folder.resolve())
            except ValueError:
                logger.warning(f"Path escapes content_folder: {filename}")
                return None
            return resolved

        return None

    def process_file(self, filename: str) -> Optional[str]:
        """
        Обработать файл и вернуть Gutenberg-форматированный контент.

        Args:
            filename: Путь к файлу (относительно корня проекта или content_folder)

        Returns:
            Gutenberg-форматированный HTML или None при ошибке
        """
        file_path = self._resolve_file_path(filename)

        if not file_path:
            logger.error(f"Content file not found: {filename}")
            return None

        try:
            # Читаем содержимое файла
            content = file_path.read_text(encoding="utf-8")

            # Проверяем расширение файла
            if file_path.suffix.lower() in [".md", ".markdown"]:
                # Конвертируем markdown в HTML
                logger.debug(f"Converting markdown to HTML: {filename}")
                html_content = markdown.markdown(
                    content,
                    extensions=['extra', 'nl2br', 'sane_lists']
                )
            elif file_path.suffix.lower() in [".html", ".htm"]:
                # Уже HTML
                logger.debug(f"Processing HTML file: {filename}")
                html_content = content
            else:
                logger.warning(f"Unknown file extension for {filename}, treating as HTML")
                html_content = content

            # Конвертируем HTML в Gutenberg блоки
            conversion = self.gutenberg_converter.convert_report(html_content)
            for warning in conversion.warnings:
                logger.warning(f"{filename}: {warning}")
            gutenberg_content = conversion.blocks

            logger.info(f"Successfully processed content file: {filename}")
            return gutenberg_content

        except FileNotFoundError:
            logger.error(f"Content file not found: {filename}")
            return None
        except UnicodeDecodeError as e:
            logger.error(f"Encoding error in content file {filename}: {e}")
            return None
        except Exception as e:
            logger.exception(f"Unexpected error processing content file {filename}: {e}")
            return None

    def validate_file_exists(self, filename: str) -> bool:
        """
        Проверить существование файла.

        Args:
            filename: Путь к файлу (относительно корня проекта или content_folder)

        Returns:
            True если файл существует
        """
        return self._resolve_file_path(filename) is not None
