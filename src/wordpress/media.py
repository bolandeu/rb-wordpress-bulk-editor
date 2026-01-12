"""
Загрузка медиафайлов в WordPress
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
import httpx
from loguru import logger

from .client import WordPressClient
from ..exceptions import MediaUploadError


class MediaEndpoint:
    """Endpoint для работы с медиафайлами"""

    def __init__(self, client: WordPressClient):
        self.client = client
        self.endpoint = "/wp-json/wp/v2/media"

    async def upload_file(
        self,
        file_path: Path,
        title: Optional[str] = None,
        alt_text: Optional[str] = None,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Загрузить локальный файл в медиатеку WordPress.

        Args:
            file_path: Путь к файлу
            title: Заголовок медиафайла
            alt_text: Alt текст для изображения
            caption: Подпись

        Returns:
            Данные загруженного медиафайла с ID

        Raises:
            MediaUploadError: Если загрузка не удалась
        """
        if not file_path.exists():
            raise MediaUploadError(f"Файл не найден: {file_path}")

        logger.debug(f"Uploading media file: {file_path}")

        try:
            # Открываем файл
            with open(file_path, "rb") as f:
                file_data = f.read()

            # Определяем MIME type
            mime_type = self._get_mime_type(file_path)

            # Данные для загрузки
            files = {
                "file": (file_path.name, file_data, mime_type)
            }

            # Метаданные
            data = {}
            if title:
                data["title"] = title
            if alt_text:
                data["alt_text"] = alt_text
            if caption:
                data["caption"] = caption

            # Загружаем через multipart/form-data
            result = await self.client.post(self.endpoint, data=data, files=files)

            logger.info(
                f"Uploaded media: {file_path.name} -> ID={result['id']} "
                f"URL={result.get('source_url', 'N/A')}"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to upload media {file_path}: {e}")
            raise MediaUploadError(f"Ошибка загрузки медиафайла: {e}")

    async def upload_from_url(
        self,
        url: str,
        title: Optional[str] = None,
        alt_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Загрузить медиафайл по URL (sideload).

        Args:
            url: URL медиафайла
            title: Заголовок
            alt_text: Alt текст

        Returns:
            Данные загруженного медиафайла

        Raises:
            MediaUploadError: Если загрузка не удалась
        """
        logger.debug(f"Downloading and uploading media from URL: {url}")

        try:
            # Скачиваем файл
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(url, follow_redirects=True)
                response.raise_for_status()
                file_data = response.content

            # Определяем имя файла из URL
            filename = url.split("/")[-1].split("?")[0]
            if not filename:
                filename = "downloaded_file"

            # MIME type из заголовков или угадываем
            mime_type = response.headers.get("content-type", "application/octet-stream")

            # Загружаем в WordPress
            files = {
                "file": (filename, file_data, mime_type)
            }

            data = {}
            if title:
                data["title"] = title
            if alt_text:
                data["alt_text"] = alt_text

            result = await self.client.post(self.endpoint, data=data, files=files)

            logger.info(
                f"Uploaded media from URL: {url} -> ID={result['id']} "
                f"URL={result.get('source_url', 'N/A')}"
            )

            return result

        except httpx.HTTPError as e:
            logger.error(f"Failed to download media from {url}: {e}")
            raise MediaUploadError(f"Ошибка скачивания медиафайла: {e}")
        except Exception as e:
            logger.error(f"Failed to upload media from URL {url}: {e}")
            raise MediaUploadError(f"Ошибка загрузки медиафайла: {e}")

    def _get_mime_type(self, file_path: Path) -> str:
        """
        Определить MIME type файла.

        Args:
            file_path: Путь к файлу

        Returns:
            MIME type
        """
        # Простой маппинг по расширению
        extension = file_path.suffix.lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".zip": "application/zip",
            ".mp4": "video/mp4",
            ".mp3": "audio/mpeg",
        }

        return mime_types.get(extension, "application/octet-stream")

    async def get(self, media_id: int) -> Dict[str, Any]:
        """Получить медиафайл по ID."""
        endpoint = f"{self.endpoint}/{media_id}"
        return await self.client.get(endpoint)

    async def get_all(
        self,
        per_page: int = 100,
        page: int = 1,
        media_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получить список всех медиафайлов с пагинацией.

        Args:
            per_page: Количество записей на страницу
            page: Номер страницы
            media_type: Фильтр по типу (image, video, audio, application)

        Returns:
            Список медиафайлов
        """
        params = {
            "per_page": per_page,
            "page": page,
            "status": "inherit"  # Получаем все медиа (включая прикрепленные)
        }

        if media_type:
            params["media_type"] = media_type

        logger.debug(f"Fetching media page {page} (per_page={per_page})")
        return await self.client.get(self.endpoint, params)

    async def fetch_all_media(self) -> List[Dict[str, Any]]:
        """
        Получить ВСЕ медиафайлы с автоматической пагинацией.

        Returns:
            Полный список медиафайлов
        """
        all_media = []
        page = 1
        per_page = 100

        logger.info("Fetching all media from WordPress...")

        while True:
            try:
                media_page = await self.get_all(per_page=per_page, page=page)

                if not media_page or (isinstance(media_page, list) and len(media_page) == 0):
                    break

                if isinstance(media_page, dict):
                    # Одна запись
                    all_media.append(media_page)
                    break
                else:
                    # Список записей
                    all_media.extend(media_page)

                    if len(media_page) < per_page:
                        break

                page += 1

            except Exception as e:
                logger.warning(f"Error fetching media page {page}: {e}")
                break

        logger.info(f"Fetched {len(all_media)} media files")
        return all_media

    async def update(
        self,
        media_id: int,
        title: Optional[str] = None,
        alt_text: Optional[str] = None,
        caption: Optional[str] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Обновить метаданные медиафайла.

        Args:
            media_id: ID медиафайла
            title: Новый заголовок
            alt_text: Новый alt текст
            caption: Новая подпись
            description: Новое описание

        Returns:
            Обновленные данные медиафайла
        """
        endpoint = f"{self.endpoint}/{media_id}"
        payload = {}

        if title is not None:
            payload["title"] = title
        if alt_text is not None:
            payload["alt_text"] = alt_text
        if caption is not None:
            payload["caption"] = caption
        if description is not None:
            payload["description"] = description

        logger.debug(f"Updating media ID={media_id}: {list(payload.keys())}")
        result = await self.client.post(endpoint, payload)

        logger.info(f"Updated media ID={media_id}")
        return result

    async def delete(self, media_id: int, force: bool = True) -> Dict[str, Any]:
        """
        Удалить медиафайл.

        Args:
            media_id: ID медиафайла
            force: True - удалить полностью, False - в корзину

        Returns:
            Результат удаления
        """
        endpoint = f"{self.endpoint}/{media_id}"
        params = {"force": "true" if force else "false"}

        logger.debug(f"Deleting media ID={media_id} (force={force})")
        result = await self.client.delete(endpoint, params)

        logger.info(f"Deleted media ID={media_id}")
        return result

    async def download_media(
        self,
        media_data: Dict[str, Any],
        output_folder: Path
    ) -> Optional[Path]:
        """
        Скачать медиафайл на диск.

        Args:
            media_data: Данные медиафайла из API
            output_folder: Папка для сохранения

        Returns:
            Path к сохраненному файлу или None при ошибке
        """
        try:
            source_url = media_data.get("source_url")
            if not source_url:
                logger.warning(f"No source_url for media {media_data.get('id')}")
                return None

            media_id = media_data["id"]
            mime_type = media_data.get("mime_type", "")

            # Определяем расширение
            extension = self._get_extension_from_mime(mime_type, source_url)

            # Создаем имя файла
            filename = f"{media_id}{extension}"
            file_path = output_folder / filename

            # Проверяем существование
            if file_path.exists():
                logger.debug(f"Media file already exists: {filename}")
                return file_path

            # Скачиваем
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                response = await http_client.get(source_url)
                if response.status_code == 200:
                    file_path.write_bytes(response.content)
                    logger.debug(f"Downloaded media ID={media_id}: {filename}")
                    return file_path
                else:
                    logger.warning(f"Failed to download media {source_url}: {response.status_code}")
                    return None

        except Exception as e:
            logger.warning(f"Failed to download media {media_data.get('id')}: {e}")
            return None

    def _get_extension_from_mime(self, mime_type: str, url: str) -> str:
        """
        Получить расширение файла из MIME type или URL.

        Args:
            mime_type: MIME type
            url: URL файла

        Returns:
            Расширение с точкой
        """
        # Сначала пробуем из MIME type
        mime_extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
            "video/mp4": ".mp4",
            "audio/mpeg": ".mp3",
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/zip": ".zip",
        }

        if mime_type in mime_extensions:
            return mime_extensions[mime_type]

        # Пробуем извлечь из URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path
        extension = Path(path).suffix.lower()

        if extension:
            return extension

        # По умолчанию
        if "image" in mime_type:
            return ".jpg"
        elif "video" in mime_type:
            return ".mp4"
        elif "audio" in mime_type:
            return ".mp3"
        else:
            return ".bin"
