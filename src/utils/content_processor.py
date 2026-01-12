"""
Обработка контента из markdown/html файлов
"""

from pathlib import Path
from typing import Optional
import markdown
from loguru import logger

from .html_to_gutenberg import HtmlToGutenberg


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

        Args:
            filename: Имя файла или путь

        Returns:
            Path к файлу или None если не найден
        """
        file_path = Path(filename)

        # Если абсолютный путь
        if file_path.is_absolute():
            return file_path if file_path.exists() else None

        # Сначала проверяем от корня проекта
        if file_path.exists():
            return file_path

        # Затем в content_folder как fallback
        fallback_path = self.content_folder / filename
        if fallback_path.exists():
            return fallback_path

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
            gutenberg_content = self.gutenberg_converter.convert(html_content)

            logger.info(f"Successfully processed content file: {filename}")
            return gutenberg_content

        except Exception as e:
            logger.error(f"Error processing content file {filename}: {e}")
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
