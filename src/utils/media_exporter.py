"""
Экспорт медиафайлов из WordPress
"""

from pathlib import Path
from typing import Dict, List, Any, Optional
import csv
import asyncio
from loguru import logger

from ..wordpress.media import MediaEndpoint


class MediaExporter:
    """Экспорт медиафайлов из WordPress с метаданными."""

    def __init__(
        self,
        media_endpoint: MediaEndpoint,
        export_folder: Path,
        download_files: bool = True,
        concurrency: int = 10
    ):
        """
        Args:
            media_endpoint: MediaEndpoint для работы с API
            export_folder: Папка для экспорта
            download_files: Скачивать ли файлы (True) или только метаданные (False)
            concurrency: Максимум параллельных загрузок
        """
        self.media_endpoint = media_endpoint
        self.export_folder = export_folder
        self.download_files = download_files
        self.concurrency = concurrency
        self.csv_data = []
        self._semaphore = asyncio.Semaphore(concurrency)

    async def export_all_media(self) -> int:
        """
        Экспортировать все медиафайлы.

        Returns:
            Количество экспортированных медиафайлов
        """
        logger.info("Starting media export...")

        # Создаем основную папку
        self.export_folder.mkdir(parents=True, exist_ok=True)

        # Получаем все медиафайлы
        all_media = await self.media_endpoint.fetch_all_media()

        if not all_media:
            logger.info("No media files found")
            self.save_csv_index()
            return 0

        # Экспортируем параллельно
        if self.download_files:
            logger.info(f"Processing {len(all_media)} media files with concurrency={self.concurrency}")
            tasks = [self._export_single_media(media) for media in all_media]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Подсчет
            count = sum(1 for r in results if r is True)
            errors = sum(1 for r in results if isinstance(r, Exception))

            if errors > 0:
                logger.warning(f"Failed to export {errors} media files")

            logger.info(f"Exported {count} media files")
        else:
            # Только метаданные
            for media in all_media:
                self._add_to_csv(media, None)
            logger.info(f"Collected metadata for {len(all_media)} media files")

        # Сохраняем CSV индекс
        self.save_csv_index()

        return len(all_media)

    async def _export_single_media(self, media: Dict[str, Any]) -> bool:
        """
        Экспортировать один медиафайл.

        Args:
            media: Данные медиафайла

        Returns:
            True если успешно
        """
        async with self._semaphore:
            try:
                media_id = media["id"]

                # Скачиваем файл если нужно
                file_path = None
                if self.download_files:
                    file_path = await self.media_endpoint.download_media(
                        media,
                        self.export_folder
                    )

                # Добавляем в CSV
                self._add_to_csv(media, file_path)

                return True

            except Exception as e:
                logger.error(f"Failed to export media {media.get('id')}: {e}")
                return False

    def _add_to_csv(
        self,
        media: Dict[str, Any],
        file_path: Optional[Path]
    ):
        """
        Добавить медиафайл в CSV индекс.

        Args:
            media: Данные медиафайла
            file_path: Путь к скачанному файлу или None
        """
        # Извлекаем заголовок
        title = media.get("title", {})
        if isinstance(title, dict):
            title_text = title.get("rendered", "")
        else:
            title_text = str(title)

        # Извлекаем caption и description
        caption = media.get("caption", {})
        if isinstance(caption, dict):
            caption_text = caption.get("rendered", "")
        else:
            caption_text = str(caption) if caption else ""

        description = media.get("description", {})
        if isinstance(description, dict):
            description_text = description.get("rendered", "")
        else:
            description_text = str(description) if description else ""

        # Относительный путь к файлу
        file_relative = ""
        if file_path:
            file_relative = file_path.relative_to(self.export_folder).as_posix()

        # Формируем запись
        self.csv_data.append({
            "id": media.get("id", ""),
            "date": media.get("date", ""),
            "modified": media.get("modified", ""),
            "title": title_text,
            "type": media.get("type", ""),
            "slug": media.get("slug", ""),
            "link": media.get("link", ""),
            "status": media.get("status", ""),
            "guid": media.get("guid", {}).get("rendered", ""),
            "caption": caption_text,
            "alt_text": media.get("alt_text", ""),
            "media_type": media.get("media_type", ""),
            "mime_type": media.get("mime_type", ""),
            "post": media.get("post", ""),
            "source_url": media.get("source_url", ""),
            "file_path": file_relative
        })

    def save_csv_index(self):
        """Сохранить CSV индекс всех медиафайлов."""
        csv_path = self.export_folder / "media_index.csv"

        logger.info(f"Saving media index to: {csv_path}")

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            fieldnames = [
                "id", "date", "modified", "title", "type", "slug", "link",
                "status", "guid", "caption", "alt_text", "media_type",
                "mime_type", "post", "source_url", "file_path"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")

            writer.writeheader()
            writer.writerows(self.csv_data)

        logger.info(f"Saved {len(self.csv_data)} media records to CSV")
