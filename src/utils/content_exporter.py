"""
Экспорт контента из WordPress в файлы
"""

from pathlib import Path
from typing import Dict, List, Any, Optional
import csv
import asyncio
from loguru import logger
from bs4 import BeautifulSoup
import httpx

from ..wordpress.client import WordPressClient
from ..constants import DEFAULT_SKIP_POST_TYPE_PATTERNS
from .wp_helpers import wp_rendered


class ContentExporter:
    """Экспорт контента из WordPress в файлы с организацией по типам."""

    def __init__(
        self,
        wp_client: WordPressClient,
        export_folder: Path,
        concurrency: int = 10,
        skip_post_type_patterns: Optional[list] = None,
        download_timeout: float = 30.0,
    ):
        """
        Args:
            wp_client: WordPress клиент
            export_folder: Папка для экспорта
            concurrency: Максимум параллельных загрузок (по умолчанию 10)
            skip_post_type_patterns: Паттерны для пропуска типов записей (из .env)
            download_timeout: Timeout для скачивания изображений (секунды)
        """
        self.wp_client = wp_client
        self.export_folder = export_folder
        self.concurrency = concurrency
        self.download_timeout = download_timeout
        self.csv_data = []
        self._semaphore = asyncio.Semaphore(concurrency)
        self.skip_post_type_patterns = skip_post_type_patterns or DEFAULT_SKIP_POST_TYPE_PATTERNS

    async def discover_post_types(self) -> List[str]:
        """
        Получить список всех доступных типов контента.

        Returns:
            Список названий post types
        """
        logger.info("Discovering available post types...")

        try:
            # WordPress REST API endpoint для получения типов контента
            response = await self.wp_client.get("/wp-json/wp/v2/types")

            post_types = []
            for type_key, type_data in response.items():
                # Пропускаем служебные типы по паттернам
                should_skip = any(pattern in type_key for pattern in self.skip_post_type_patterns)

                if should_skip:
                    logger.debug(f"Skipping post type: {type_key} (matches skip pattern)")
                else:
                    if type_key == "post":
                        type_key = "posts"
                    elif type_key == "page":
                        type_key = "pages"
                    post_types.append(type_key)
                    logger.debug(f"Found post type: {type_key} - {type_data.get('name', 'N/A')}")

            logger.info(f"Discovered {len(post_types)} post types: {', '.join(post_types)}")
            return post_types

        except Exception as e:
            logger.error(f"Failed to discover post types: {e}")
            # Fallback to standard types
            return ["posts", "pages"]

    async def _prefetch_media_cache(self, posts: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """
        Batch-загрузка метаданных медиафайлов для всех постов (устраняет N+1 запросы).

        Args:
            posts: Список постов

        Returns:
            Словарь {media_id: media_data}
        """
        media_ids = set()
        for post in posts:
            mid = post.get("featured_media")
            if mid and mid != 0:
                media_ids.add(mid)

        if not media_ids:
            return {}

        logger.info(f"Prefetching {len(media_ids)} media items...")
        cache: Dict[int, Dict[str, Any]] = {}

        # Загружаем пачками по 100 через include параметр
        media_id_list = list(media_ids)
        for i in range(0, len(media_id_list), 100):
            batch = media_id_list[i : i + 100]
            try:
                params = {
                    "include": ",".join(str(mid) for mid in batch),
                    "per_page": 100,
                }
                media_items = await self.wp_client.get("/wp-json/wp/v2/media", params)
                if isinstance(media_items, list):
                    for item in media_items:
                        cache[item["id"]] = item
                logger.debug(f"Prefetched media batch {i // 100 + 1}: {len(batch)} items")
            except Exception as e:
                logger.warning(f"Failed to prefetch media batch: {e}")

        logger.info(f"Media cache ready: {len(cache)} items")
        return cache

    async def export_post_type(self, post_type: str) -> int:
        """
        Экспортировать все записи указанного типа.

        Args:
            post_type: Тип контента (posts, pages, custom)

        Returns:
            Количество экспортированных записей
        """
        logger.info(f"Exporting post type: {post_type}")

        # Получаем все записи этого типа
        all_posts = await self._fetch_all_posts(post_type)

        # Если нет записей - пропускаем создание папки
        if not all_posts:
            logger.info(f"Exported 0 records for {post_type}")
            return 0

        # Создаем папку только если есть записи
        type_folder = self.export_folder / post_type
        type_folder.mkdir(parents=True, exist_ok=True)

        # Batch prefetch медиа-метаданных (устраняет N+1)
        media_cache = await self._prefetch_media_cache(all_posts)

        # Экспортируем посты параллельно
        logger.info(f"Processing {len(all_posts)} posts with concurrency={self.concurrency}")
        tasks = [self._export_single_post(post, post_type, type_folder, media_cache) for post in all_posts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Подсчитываем успешные экспорты
        count = sum(1 for r in results if r is True)
        errors = sum(1 for r in results if isinstance(r, Exception))

        if errors > 0:
            logger.warning(f"Failed to export {errors} posts from {post_type}")

        logger.info(f"Exported {count} records for {post_type}")
        return count

    async def _export_single_post(
        self,
        post: Dict[str, Any],
        post_type: str,
        type_folder: Path,
        media_cache: Optional[Dict[int, Dict[str, Any]]] = None,
    ) -> bool:
        """
        Экспортировать один пост (используется для параллельной обработки).

        Args:
            post: Данные поста
            post_type: Тип контента
            type_folder: Папка для сохранения

        Returns:
            True если успешно, False или Exception если ошибка
        """
        async with self._semaphore:
            try:
                post_id = post.get("id")
                if post_id is None:
                    logger.warning("Post missing 'id' field, skipping")
                    return False

                # Санитизация post_id для безопасного имени файла
                safe_id = str(post_id).replace("/", "_").replace("\\", "_").replace("..", "_")

                # Извлекаем контент
                content = self._extract_content(post)

                # Сохраняем HTML только если контент не пустой И файл не существует
                html_path = None
                if content and content.strip():
                    file_path = type_folder / f"{safe_id}.html"
                    if file_path.exists():
                        logger.debug(f"HTML already exists for post {post_id}, skipping")
                        html_path = file_path
                    else:
                        file_path.write_text(content, encoding="utf-8")
                        html_path = file_path
                        logger.debug(f"Saved HTML for post {post_id}")
                else:
                    logger.debug(f"Skipped empty HTML for post {post_id}")

                # Скачиваем featured image независимо от контента
                image_path = await self._download_featured_image(post, type_folder, post_id, safe_id, media_cache)

                # Добавляем в CSV ВСЕГДА (даже если файлы уже существовали)
                # html_path и image_path содержат пути к файлам (существующим или новым)
                # или None если файлов нет
                self._add_to_csv(post_type, post, html_path, image_path)

                return True

            except Exception as e:
                logger.error(f"Failed to export post {post.get('id')}: {e}")
                return False

    async def _fetch_all_posts(self, post_type: str) -> List[Dict[str, Any]]:
        """
        Получить все записи указанного типа с пагинацией.

        Args:
            post_type: Тип контента

        Returns:
            Список всех записей
        """
        all_posts = []
        page = 1
        per_page = 100

        while True:
            try:
                endpoint = f"/wp-json/wp/v2/{post_type}"
                params = {
                    "per_page": per_page,
                    "page": page,
                    "status": "any",  # Получаем все статусы
                }

                posts = await self.wp_client.get(endpoint, params)

                if not posts or (isinstance(posts, list) and len(posts) == 0):
                    break

                if isinstance(posts, dict):
                    # Одна запись
                    all_posts.append(posts)
                    break
                else:
                    # Список записей
                    all_posts.extend(posts)

                    if len(posts) < per_page:
                        break

                page += 1

            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(f"Error fetching page {page} of {post_type}: {e}")
                if page == 1:
                    # Первая страница не загрузилась — тип контента недоступен
                    break
                # Для последующих страниц — возвращаем что уже собрали
                logger.info(f"Returning {len(all_posts)} posts fetched before error")
                break

        return all_posts

    def _extract_content(self, post: Dict[str, Any]) -> str:
        """
        Извлечь HTML контент из записи.

        Args:
            post: Данные записи

        Returns:
            HTML контент
        """
        return wp_rendered(post.get("content", {}))

    async def _download_featured_image(
        self,
        post: Dict[str, Any],
        type_folder: Path,
        post_id: int,
        safe_id: Optional[str] = None,
        media_cache: Optional[Dict[int, Dict[str, Any]]] = None,
    ) -> Optional[Path]:
        """
        Скачать featured image для записи.

        Args:
            post: Данные записи
            type_folder: Папка типа контента
            post_id: ID записи
            safe_id: Безопасный ID для имени файла
            media_cache: Кэш метаданных медиа (batch prefetch)

        Returns:
            Path к сохраненному изображению или None
        """
        try:
            # Получаем ID featured media
            featured_media_id = post.get("featured_media")
            if not featured_media_id or featured_media_id == 0:
                logger.debug(f"No featured media for post {post_id}")
                return None

            # Получаем данные медиафайла из кэша или API
            if media_cache and featured_media_id in media_cache:
                media_data = media_cache[featured_media_id]
            else:
                media_endpoint = f"/wp-json/wp/v2/media/{featured_media_id}"
                media_data = await self.wp_client.get(media_endpoint)

            # Получаем URL изображения
            source_url = media_data.get("source_url")
            if not source_url:
                logger.warning(f"No source_url for media {featured_media_id}")
                return None

            # Определяем расширение из URL или content-type
            extension = self._get_extension_from_url(source_url)

            # Проверяем существование файла
            file_id = safe_id or str(post_id)
            image_filename = f"{file_id}{extension}"
            image_path = type_folder / image_filename

            if image_path.exists():
                logger.debug(f"Image already exists for post {post_id}, skipping download")
                return image_path

            # Скачиваем изображение
            async with httpx.AsyncClient(timeout=self.download_timeout) as client:
                response = await client.get(source_url)
                if response.status_code == 200:
                    image_path.write_bytes(response.content)
                    logger.debug(f"Downloaded featured image for post {post_id}: {image_filename}")
                    return image_path
                else:
                    logger.warning(f"Failed to download image from {source_url}: {response.status_code}")
                    return None

        except Exception as e:
            logger.warning(f"Failed to download featured image for post {post_id}: {e}")
            return None

    def _get_extension_from_url(self, url: str) -> str:
        """
        Получить расширение файла из URL.

        Args:
            url: URL изображения

        Returns:
            Расширение с точкой (например .jpg)
        """
        # Пытаемся извлечь расширение из URL
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path

        # Получаем расширение из пути
        extension = Path(path).suffix.lower()

        # Если расширения нет или оно странное, используем .jpg по умолчанию
        valid_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}
        if extension not in valid_extensions:
            extension = ".jpg"

        return extension

    def _add_to_csv(self, post_type: str, post: Dict[str, Any], html_path: Optional[Path], image_path: Optional[Path]):
        """
        Добавить запись в CSV индекс.

        Args:
            post_type: Тип контента
            post: Данные записи
            html_path: Путь к HTML файлу или None
            image_path: Путь к изображению или None
        """
        # Извлекаем заголовок (убираем HTML теги)
        title_raw = wp_rendered(post.get("title", {}))
        title_text = BeautifulSoup(title_raw, "html.parser").get_text() if title_raw else ""

        # Относительные пути к файлам
        html_relative = ""
        if html_path:
            html_relative = html_path.relative_to(self.export_folder).as_posix()

        image_relative = ""
        if image_path:
            image_relative = image_path.relative_to(self.export_folder).as_posix()

        # SEO title (может быть в разных полях в зависимости от плагина)
        seo_title = (
            post.get("seo_title") or post.get("yoast_head_json", {}).get("title") or post.get("rank_math_title") or ""
        )

        seo_description = (
            post.get("seo_description")
            or post.get("yoast_head_json", {}).get("description")
            or post.get("rank_math_description")
            or ""
        )

        self.csv_data.append(
            {
                "post_type": post_type,
                "id": post.get("id", ""),
                "title": title_text,
                "slug": post.get("slug", ""),
                "link": post.get("link", ""),
                "html_path": html_relative,
                "image_path": image_relative,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "status": post.get("status", ""),
                "date": post.get("date", ""),
            }
        )

    def save_csv_index(self):
        """Сохранить CSV индекс всех экспортированных записей."""
        csv_path = self.export_folder / "index.csv"

        logger.info(f"Saving CSV index to: {csv_path}")

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            fieldnames = [
                "post_type",
                "id",
                "title",
                "slug",
                "link",
                "html_path",
                "image_path",
                "seo_title",
                "seo_description",
                "status",
                "date",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")

            writer.writeheader()
            writer.writerows(self.csv_data)

        logger.info(f"Saved {len(self.csv_data)} records to CSV index")

    async def export_media_metadata(self) -> int:
        """
        Экспортировать метаданные медиафайлов (без скачивания файлов).

        Returns:
            Количество экспортированных медиафайлов
        """
        from ..wordpress.media import MediaEndpoint

        logger.info("Exporting media metadata...")

        # Создаем endpoint
        media_endpoint = MediaEndpoint(self.wp_client)

        # Получаем все медиафайлы
        all_media = await media_endpoint.fetch_all_media()

        if not all_media:
            logger.info("No media files found")
            return 0

        # Сохраняем CSV с метаданными медиа
        media_csv_path = self.export_folder / "media.csv"

        logger.info(f"Saving media metadata to: {media_csv_path}")

        with open(media_csv_path, "w", encoding="utf-8-sig", newline="") as f:
            fieldnames = [
                "id",
                "date",
                "modified",
                "title",
                "type",
                "slug",
                "link",
                "status",
                "guid",
                "caption",
                "alt_text",
                "media_type",
                "mime_type",
                "post",
                "source_url",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
            writer.writeheader()

            for media in all_media:
                writer.writerow(
                    {
                        "id": media.get("id", ""),
                        "date": media.get("date", ""),
                        "modified": media.get("modified", ""),
                        "title": wp_rendered(media.get("title", {})),
                        "type": media.get("type", ""),
                        "slug": media.get("slug", ""),
                        "link": media.get("link", ""),
                        "status": media.get("status", ""),
                        "guid": wp_rendered(media.get("guid", {})),
                        "caption": wp_rendered(media.get("caption", {})),
                        "alt_text": media.get("alt_text", ""),
                        "media_type": media.get("media_type", ""),
                        "mime_type": media.get("mime_type", ""),
                        "post": media.get("post", ""),
                        "source_url": media.get("source_url", ""),
                    }
                )

        logger.info(f"Saved {len(all_media)} media records to CSV")
        return len(all_media)

    async def export_all(self, include_media: bool = False) -> Dict[str, int]:
        """
        Экспортировать весь контент сайта.

        Args:
            include_media: Включить экспорт метаданных медиа (без скачивания файлов)

        Returns:
            Статистика по типам контента
        """
        stats = {}

        # Создаем основную папку
        self.export_folder.mkdir(parents=True, exist_ok=True)

        # Получаем все типы контента
        post_types = await self.discover_post_types()

        # Экспортируем каждый тип
        for post_type in post_types:
            try:
                count = await self.export_post_type(post_type)
                stats[post_type] = count
            except Exception as e:
                logger.error(f"Failed to export {post_type}: {e}")
                stats[post_type] = 0

        # Экспортируем медиа если указано
        if include_media:
            try:
                media_count = await self.export_media_metadata()
                stats["media"] = media_count
            except Exception as e:
                logger.error(f"Failed to export media: {e}")
                stats["media"] = 0

        # Сохраняем CSV индекс
        self.save_csv_index()

        return stats
