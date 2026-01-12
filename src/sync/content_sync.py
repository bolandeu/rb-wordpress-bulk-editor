"""
Синхронизация контента (posts, pages, custom types)
"""

from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd
from loguru import logger

from ..wordpress.client import WordPressClient
from ..wordpress.posts import PostsEndpoint
from ..wordpress.taxonomies import TaxonomiesEndpoint, TaxonomyCache, normalize_taxonomy_name
from ..wordpress.media import MediaEndpoint
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..mappers.content_mapper import ContentMapper
from ..mappers.field_detector import FieldDetector
from ..config import AppConfig
from ..exceptions import ValidationError
from ..utils.content_processor import ContentProcessor


class ContentSync:
    """Синхронизация контента между Excel и WordPress"""

    def __init__(
        self,
        wp_client: WordPressClient,
        config: AppConfig,
        taxonomy_cache: TaxonomyCache
    ):
        self.wp_client = wp_client
        self.config = config
        self.taxonomy_cache = taxonomy_cache

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> Dict[str, int]:
        """
        Синхронизировать один лист Excel.

        Args:
            sheet_name: Название листа (= тип контента)
            df: DataFrame с данными листа
            excel_writer: Writer для обратной записи (если нужна)

        Returns:
            Статистика: {added: N, updated: N, deleted: N, errors: N, skipped: N}
        """
        stats = {
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "errors": 0,
            "skipped": 0
        }

        # Создаем endpoints
        posts_endpoint = PostsEndpoint(self.wp_client, sheet_name)
        media_endpoint = MediaEndpoint(self.wp_client)

        # Создаем mapper и content processor
        mapper = ContentMapper(df.columns.tolist())
        content_processor = ContentProcessor(self.config.paths.content_folder)

        # Обрабатываем в порядке: delete → update → add
        for action in ["delete", "update", "add"]:
            # Фильтруем строки по action
            action_rows = df[df["action"].str.lower().str.strip() == action]

            if len(action_rows) == 0:
                continue

            logger.info(f"Processing {action}: {len(action_rows)} rows")

            for idx, row in action_rows.iterrows():
                try:
                    # Валидация
                    is_valid, error_msg = mapper.validate_row(row, action)
                    if not is_valid:
                        logger.warning(f"Row {idx}: Validation failed: {error_msg}")
                        stats["errors"] += 1
                        continue

                    # Обработка в зависимости от action
                    if action == "delete":
                        result = await self._delete_item(
                            posts_endpoint,
                            row
                        )
                    elif action == "update":
                        result = await self._update_item(
                            posts_endpoint,
                            media_endpoint,
                            mapper,
                            content_processor,
                            row
                        )
                    else:  # add
                        result = await self._add_item(
                            posts_endpoint,
                            media_endpoint,
                            mapper,
                            content_processor,
                            row
                        )

                    # Обновляем статистику
                    if action == "add":
                        stats["added"] += 1
                    elif action == "update":
                        stats["updated"] += 1
                    elif action == "delete":
                        stats["deleted"] += 1

                    # Записываем обратно в Excel
                    if excel_writer and result and self.config.sync.update_excel:
                        excel_writer.update_from_api_response(
                            sheet_name,
                            idx,
                            result,
                            action
                        )

                except Exception as e:
                    logger.error(f"Row {idx}: Error during {action}: {e}")
                    stats["errors"] += 1

        # Подсчитываем пропущенные
        valid_actions = {"add", "update", "delete"}
        skipped = df[~df["action"].str.lower().str.strip().isin(valid_actions)]
        stats["skipped"] = len(skipped)

        return stats

    async def _add_item(
        self,
        posts_endpoint: PostsEndpoint,
        media_endpoint: MediaEndpoint,
        mapper: ContentMapper,
        content_processor: ContentProcessor,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Создать новую запись."""
        # 1. Обрабатываем таксономии
        taxonomy_ids = await self._process_taxonomies(row, mapper)

        # 2. Создаем payload
        payload = mapper.map_row(row, taxonomy_ids)

        # 3. Обрабатываем content_path если заполнен
        if "content_path" in row.index and pd.notna(row["content_path"]) and row["content_path"] != "":
            content_filename = str(row["content_path"]).strip()

            if content_processor.validate_file_exists(content_filename):
                logger.debug(f"Processing content from file: {content_filename}")
                gutenberg_content = content_processor.process_file(content_filename)
                if gutenberg_content:
                    payload["content"] = gutenberg_content
                    logger.debug(f"Content loaded from file: {content_filename}")
                else:
                    logger.warning(f"Failed to process content file: {content_filename}")
            else:
                logger.warning(f"Content file not found: {content_filename}")

        # 4. Проверяем дубликаты по slug (если slug заполнен)
        slug = payload.get("slug")
        if slug and slug.strip():
            existing_post = await posts_endpoint.find_by_slug(slug.strip())
            if existing_post:
                # Запись с таким slug уже существует - делаем update вместо add
                post_id = existing_post["id"]
                logger.warning(
                    f"Post with slug='{slug}' already exists (ID={post_id}). "
                    f"Converting action from 'add' to 'update'."
                )
                result = await posts_endpoint.update(post_id, payload)
                logger.info(f"Updated existing post ID={post_id} (found by slug='{slug}')")
            else:
                # Slug свободен - создаем новую запись
                logger.debug(f"Creating post with slug='{slug}': {payload.get('title', 'No title')}")
                result = await posts_endpoint.create(payload)
                post_id = result["id"]
        else:
            # Slug не заполнен - создаем как обычно
            logger.debug(f"Creating post: {payload.get('title', 'No title')}")
            result = await posts_endpoint.create(payload)
            post_id = result["id"]

        # 5. Загружаем медиа (если есть img_path)
        if not self.config.sync.skip_media and mapper.should_upload_media(row):
            media_path = mapper.extract_media_path(row)
            title = row.get("title", "")

            try:
                media_result = await self._upload_media(
                    media_endpoint,
                    media_path,
                    title
                )

                # Обновляем пост с featured_media
                if media_result:
                    await posts_endpoint.update(
                        post_id,
                        {"featured_media": media_result["id"]}
                    )
                    result["featured_media"] = media_result["id"]
                    result["image_url"] = media_result.get("source_url")

            except Exception as e:
                logger.warning(f"Failed to upload media for post {post_id}: {e}")

        return result

    async def _update_item(
        self,
        posts_endpoint: PostsEndpoint,
        media_endpoint: MediaEndpoint,
        mapper: ContentMapper,
        content_processor: ContentProcessor,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Обновить существующую запись."""
        post_id = int(row["id"])

        # 1. Обрабатываем таксономии
        taxonomy_ids = await self._process_taxonomies(row, mapper)

        # 2. Создаем payload
        payload = mapper.map_row(row, taxonomy_ids)

        # 3. Обрабатываем content_path если заполнен
        if "content_path" in row.index and pd.notna(row["content_path"]) and row["content_path"] != "":
            content_filename = str(row["content_path"]).strip()

            if content_processor.validate_file_exists(content_filename):
                logger.debug(f"Processing content from file: {content_filename}")
                gutenberg_content = content_processor.process_file(content_filename)
                if gutenberg_content:
                    payload["content"] = gutenberg_content
                    logger.debug(f"Content loaded from file: {content_filename}")
                else:
                    logger.warning(f"Failed to process content file: {content_filename}")
            else:
                logger.warning(f"Content file not found: {content_filename}")

        # 4. Обновляем запись
        logger.debug(f"Updating post ID={post_id}")
        result = await posts_endpoint.update(post_id, payload)

        # 5. Обновляем медиа (если img_path заполнен - всегда обновляем, даже если featured_media уже есть)
        if not self.config.sync.skip_media and mapper.should_upload_media(row):
            media_path = mapper.extract_media_path(row)
            title = row.get("title", "")

            try:
                media_result = await self._upload_media(
                    media_endpoint,
                    media_path,
                    title
                )

                if media_result:
                    await posts_endpoint.update(
                        post_id,
                        {"featured_media": media_result["id"]}
                    )
                    result["featured_media"] = media_result["id"]
                    result["image_url"] = media_result.get("source_url")

            except Exception as e:
                logger.warning(f"Failed to upload media for post {post_id}: {e}")

        # 6. Получаем актуальные данные записи (link, modified могут отсутствовать в ответе update)
        if "link" not in result or "modified" not in result:
            try:
                fresh_data = await posts_endpoint.get(post_id)
                if "link" not in result:
                    result["link"] = fresh_data.get("link")
                if "modified" not in result:
                    result["modified"] = fresh_data.get("modified")
                logger.debug(f"Fetched fresh data for post {post_id}: link={result.get('link')}, modified={result.get('modified')}")
            except Exception as e:
                logger.warning(f"Failed to fetch updated data for post {post_id}: {e}")

        return result

    async def _delete_item(
        self,
        posts_endpoint: PostsEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Удалить запись (в корзину)."""
        post_id = int(row["id"])
        logger.debug(f"Deleting post ID={post_id}")

        # Удаляем featured media если включено
        if self.config.sync.delete_featured_media:
            try:
                # Получаем данные записи, чтобы узнать featured_media
                post_data = await posts_endpoint.get(post_id)
                featured_media_id = post_data.get("featured_media")

                if featured_media_id and featured_media_id > 0:
                    from ..wordpress.media import MediaEndpoint
                    media_endpoint = MediaEndpoint(self.wp_client)
                    await media_endpoint.delete(featured_media_id, force=True)
                    logger.info(f"Deleted featured media ID={featured_media_id} for post ID={post_id}")
            except Exception as e:
                logger.warning(f"Failed to delete featured media for post ID={post_id}: {e}")

        # Удаляем в корзину (force=False)
        result = await posts_endpoint.delete(post_id, force=False)
        return result

    async def _process_taxonomies(
        self,
        row: pd.Series,
        mapper: ContentMapper
    ) -> Dict[str, list[int]]:
        """
        Обработать таксономии из строки Excel.

        Args:
            row: pandas Series
            mapper: ContentMapper

        Returns:
            Dict {taxonomy_name: [term_ids]}
        """
        taxonomy_fields = mapper.detector.get_taxonomy_fields()
        taxonomy_ids = {}

        for tax_field in taxonomy_fields:
            if tax_field not in row.index:
                continue

            value = row[tax_field]
            if pd.isna(value) or value == "":
                continue

            # Создаем endpoint для таксономии
            normalized_tax_field = normalize_taxonomy_name(tax_field)
            tax_endpoint = TaxonomiesEndpoint(self.wp_client, normalized_tax_field)

            # Обрабатываем строку с таксономиями
            term_ids = await tax_endpoint.process_taxonomy_string(str(value))

            if term_ids:
                taxonomy_ids[tax_field] = term_ids

        return taxonomy_ids

    async def _upload_media(
        self,
        media_endpoint: MediaEndpoint,
        media_path: str,
        title: str
    ) -> Optional[Dict[str, Any]]:
        """
        Загрузить медиафайл.

        Args:
            media_endpoint: MediaEndpoint
            media_path: Путь к файлу (локальный или URL)
            title: Название для медиа

        Returns:
            Результат загрузки или None
        """
        # Проверяем это URL или локальный путь
        if media_path.startswith("http://") or media_path.startswith("https://"):
            # Загрузка по URL
            return await media_endpoint.upload_from_url(
                media_path,
                title=title,
                alt_text=title
            )
        else:
            # Локальный файл
            file_path = Path(media_path)

            if not file_path.is_absolute():
                # Сначала проверяем путь от корня проекта
                if file_path.exists():
                    # Путь относительно текущей директории (корня проекта)
                    pass
                else:
                    # Пробуем в data/media как fallback
                    fallback_path = self.config.paths.media_folder / media_path
                    if fallback_path.exists():
                        file_path = fallback_path

            if not file_path.exists():
                logger.warning(f"Media file not found: {media_path}")
                return None

            return await media_endpoint.upload_file(
                file_path,
                title=title,
                alt_text=title
            )
