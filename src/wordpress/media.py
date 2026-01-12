"""
Загрузка медиафайлов в WordPress
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
import httpx
import aiohttp
import mimetypes
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
            # Проверяем размер файла
            file_size = file_path.stat().st_size
            logger.debug(f"File size: {file_size} bytes ({file_size / 1024:.2f} KB)")

            # Открываем файл
            with open(file_path, "rb") as f:
                file_data = f.read()

            # Определяем MIME type
            mime_type = self._get_mime_type(file_path)
            logger.debug(f"MIME type: {mime_type}, Filename: {file_path.name}")

            # Данные для загрузки
            files = {
                "file": (file_path.name, file_data, mime_type)
            }

            # Загружаем файл через aiohttp по аналогии с requests
            # Файл передается напрямую, не через multipart/form-data
            logger.debug(f"Uploading file via aiohttp (direct data transfer)")
            
            # Авторизация
            auth = aiohttp.BasicAuth(self.client.config.username, self.client.config.password) if self.client.config.username and self.client.config.password else None
            
            # Настройка SSL
            ssl_context = None if self.client.config.verify_ssl else False
            
            # Заголовки
            headers = {
                "Content-Disposition": f'attachment; filename="{file_path.name}"',
                "Content-Type": mime_type,
            }
            
            # URL для запроса
            url = f"{self.client.config.url.rstrip('/')}{self.endpoint}"
            
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_context),
                timeout=aiohttp.ClientTimeout(total=120.0)  # Увеличенный таймаут для больших файлов
            ) as session:
                # Открываем файл для чтения
                with open(file_path, "rb") as f:
                    logger.debug(f"Sending POST request to {url}")
                    logger.debug(f"Headers: {headers}")
                    async with session.post(url, data=f, headers=headers, auth=auth) as response:
                        logger.debug(f"Response status: {response.status}")
                        logger.debug(f"Response headers: {dict(response.headers)}")
                        
                        if response.status >= 400:
                            error_text = await response.text()
                            raise MediaUploadError(
                                f"WordPress API error ({response.status}): {error_text[:500]}"
                            )
                        
                        # Пробуем распарсить JSON
                        try:
                            result = await response.json()
                            logger.debug(f"Response: {result.keys()}")
                        except Exception as json_error:
                            # Если JSON не парсится, пробуем извлечь ID из заголовков
                            logger.warning(f"Failed to parse JSON response: {json_error}")
                            response_text = await response.text()
                            logger.debug(f"Response text (first 500 chars): {response_text[:500]}")
                            
                            # Извлекаем ID из заголовка X-WP-Upload-Attachment-ID
                            media_id = response.headers.get('X-WP-Upload-Attachment-ID')
                            if media_id:
                                logger.info(f"Extracted media ID from header: {media_id}")
                                result = {
                                    'id': int(media_id),
                                    'source_url': f"{self.client.config.url.rstrip('/')}/wp-content/uploads/{file_path.name}"
                                }
                            else:
                                # Пробуем извлечь ID из текста ответа (если там JSON с warnings)
                                import json
                                try:
                                    # Ищем JSON в ответе после warnings
                                    lines = response_text.split('\n')
                                    json_str = None
                                    for line in lines:
                                        stripped = line.strip()
                                        if stripped.startswith('{') or stripped.startswith('['):
                                            # Нашли JSON
                                            json_str = stripped
                                            break
                                    
                                    if json_str:
                                        result = json.loads(json_str)
                                        logger.info(f"Extracted JSON from response after warnings")
                                    else:
                                        raise MediaUploadError(
                                            f"Failed to upload media: server returned non-JSON response and no ID in headers or body"
                                        )
                                except json.JSONDecodeError:
                                    raise MediaUploadError(
                                        f"Failed to upload media: server returned invalid JSON. Response: {response_text[:500]}"
                                    )

            media_id = result['id']
            logger.info(
                f"Uploaded media (file only): {file_path.name} -> ID={media_id} "
                f"URL={result.get('source_url', 'N/A')}"
            )

            # Затем обновляем метаданные отдельным запросом
            if title or alt_text or caption:
                logger.debug(f"Updating metadata for media ID={media_id}")
                await self.update(
                    media_id,
                    title=title,
                    alt_text=alt_text,
                    caption=caption
                )
                logger.debug(f"Updated metadata for media ID={media_id}")

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

            # Загружаем файл через aiohttp по аналогии с upload_file
            logger.debug(f"Uploading downloaded file via aiohttp")
            
            # Авторизация
            auth = aiohttp.BasicAuth(self.client.config.username, self.client.config.password) if self.client.config.username and self.client.config.password else None
            
            # Настройка SSL
            ssl_context = None if self.client.config.verify_ssl else False
            
            # Заголовки
            headers = {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": mime_type,
            }
            
            # URL для запроса
            upload_url = f"{self.client.config.url.rstrip('/')}{self.endpoint}"
            
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_context),
                timeout=aiohttp.ClientTimeout(total=120.0)
            ) as session:
                # Передаем данные напрямую (не через multipart/form-data)
                from io import BytesIO
                data = BytesIO(file_data)
                
                logger.debug(f"Sending POST request to {upload_url}")
                async with session.post(upload_url, data=data, headers=headers, auth=auth) as response:
                    logger.debug(f"Response status: {response.status}")
                    
                    if response.status >= 400:
                        error_text = await response.text()
                        raise MediaUploadError(
                            f"WordPress API error ({response.status}): {error_text[:500]}"
                        )
                    
                    # Пробуем распарсить JSON
                    try:
                        result = await response.json()
                        logger.debug(f"Response: {result.keys()}")
                    except Exception as json_error:
                        # Если JSON не парсится, пробуем извлечь ID из заголовков
                        logger.warning(f"Failed to parse JSON response: {json_error}")
                        response_text = await response.text()
                        logger.debug(f"Response text (first 200 chars): {response_text[:200]}")
                        
                        # Извлекаем ID из заголовка X-WP-Upload-Attachment-ID
                        media_id = response.headers.get('X-WP-Upload-Attachment-ID')
                        if media_id:
                            logger.info(f"Extracted media ID from header: {media_id}")
                            result = {
                                'id': int(media_id),
                                'source_url': f"{self.client.config.url.rstrip('/')}/wp-content/uploads/{filename}"
                            }
                        else:
                            raise MediaUploadError(
                                f"Failed to upload media: server returned non-JSON response and no ID in headers"
                            )

            media_id = result['id']
            logger.info(
                f"Uploaded media from URL (file only): {url} -> ID={media_id} "
                f"URL={result.get('source_url', 'N/A')}"
            )

            # Затем обновляем метаданные отдельным запросом
            if title or alt_text:
                await self.update(
                    media_id,
                    title=title,
                    alt_text=alt_text
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
        Определить MIME type файла с использованием стандартной библиотеки mimetypes.

        Args:
            file_path: Путь к файлу

        Returns:
            MIME type
        """
        mime_type, _ = mimetypes.guess_type(str(file_path))
        return mime_type or "application/octet-stream"

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
