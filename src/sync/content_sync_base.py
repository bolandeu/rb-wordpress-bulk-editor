"""
Базовый класс с общей логикой для ContentSync и ContentSyncAsync.

Содержит методы обработки записей, которые идентичны в обоих вариантах:
_add_item, _update_item, _delete_item, _process_taxonomies, _upload_media
"""

from abc import abstractmethod
from typing import Any, Dict, Mapping, Optional, Sequence, cast
from loguru import logger

from ..wordpress.client import WordPressClient
from ..wordpress.posts import PostsEndpoint
from ..wordpress.taxonomies import (
    TaxonomiesEndpoint,
    TaxonomyCache,
    normalize_taxonomy_name,
    primary_term_meta_key,
)
from ..wordpress.media import MediaEndpoint
from ..mappers.content_mapper import ContentMapper
from ..config import AppConfig
from ..records.base import Record, RecordWriter, has_value, is_blank
from ..utils.content_processor import ContentProcessor
from .base_sync import BaseSync


class ContentSyncBase(BaseSync):
    """
    Базовый класс для синхронизации контента.

    Содержит общую логику обработки записей.
    Наследники (ContentSync, ContentSyncAsync) реализуют только sync_records().
    """

    def __init__(self, wp_client: WordPressClient, config: AppConfig, taxonomy_cache: TaxonomyCache):
        super().__init__(wp_client, config)
        self.taxonomy_cache = taxonomy_cache

    @abstractmethod
    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
    ):
        """Синхронизировать группу записей одного типа контента."""

    def _make_posts_endpoint(self, post_type: str) -> PostsEndpoint:
        """Фабричный метод для PostsEndpoint (можно переопределить в тестах)."""
        return PostsEndpoint(self.wp_client, post_type)

    def _make_media_endpoint(self) -> MediaEndpoint:
        """Фабричный метод для MediaEndpoint (можно переопределить в тестах)."""
        return MediaEndpoint(self.wp_client)

    async def _add_item(
        self,
        posts_endpoint: PostsEndpoint,
        media_endpoint: MediaEndpoint,
        mapper: ContentMapper,
        content_processor: ContentProcessor,
        row: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Создать новую запись."""
        # Dry-run: только логируем, не отправляем
        if self.config.sync.dry_run:
            taxonomy_ids, _ = await self._process_taxonomies(row, mapper)
            payload = mapper.map_row(row, taxonomy_ids)
            logger.info(f"[DRY-RUN] Would CREATE post: {payload.get('title', 'No title')}")
            return {"id": 0, "link": "", "dry_run": True}

        # 1. Обрабатываем таксономии
        taxonomy_ids, primary_terms = await self._process_taxonomies(row, mapper)

        # 2. Создаем payload
        payload = mapper.map_row(row, taxonomy_ids)
        _cf = mapper.allowed_columns

        # 3. Убираем featured_media из payload если --skip-media
        if self.config.sync.skip_media:
            payload.pop("featured_media", None)

        # 4. Обрабатываем content_path если заполнен
        # Пропускаем если columns_filter активен и content не в фильтре
        if (not _cf or "content" in _cf) and has_value(row, "content_path"):
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

        # 5. Логируем что отправляем
        has_content = "content" in payload
        content_len = len(payload["content"]) if has_content else 0
        content_note = f"yes ({content_len} chars)" if has_content else "no"
        logger.info(f"Sending create: payload keys={list(payload.keys())}, content={content_note}")

        # 6. Проверяем дубликаты по slug (если slug заполнен)
        slug = payload.get("slug")
        if slug and slug.strip():
            existing_post = await posts_endpoint.find_by_slug(slug.strip())
            if existing_post:
                # Запись с таким slug уже существует - делаем update вместо add
                post_id = existing_post["id"]
                logger.warning(
                    f"Post with slug='{slug}' already exists (ID={post_id}). Converting action from 'add' to 'update'."
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

        # 6. Основные (primary) термины таксономий — best-effort
        await self._apply_primary_terms(posts_endpoint, post_id, primary_terms)

        # 7. Загружаем медиа (если есть img_path)
        # Пропускаем если --skip-media или columns_filter активен и img_path/featured_media не в фильтре
        _should_media = not self.config.sync.skip_media and (not _cf or "img_path" in _cf or "featured_media" in _cf)
        if _should_media and mapper.should_upload_media(row):
            media_path = cast(str, mapper.extract_media_path(row))  # should_upload_media() его гарантирует
            # Берем title и alt_text из строки
            title = row.get("title", "") if has_value(row, "title") else ""
            alt_text = row.get("alt_text", "") if has_value(row, "alt_text") else ""
            # Если alt_text не заполнен, используем title
            if not alt_text:
                alt_text = title
            caption = row.get("caption", "") if has_value(row, "caption") else ""

            try:
                logger.debug(f"Uploading media for post {post_id}: {media_path}")
                media_result = await self._upload_media(media_endpoint, media_path, title, alt_text, caption)

                # Обновляем пост с featured_media
                if media_result:
                    await posts_endpoint.update(post_id, {"featured_media": media_result["id"]})
                    result["featured_media"] = media_result["id"]
                    result["image_url"] = media_result.get("source_url")
                    logger.info(f"Attached media ID={media_result['id']} to post ID={post_id}")
                else:
                    logger.warning("Media upload returned None, skipping featured_media update")

            except Exception as e:
                logger.warning(f"Failed to upload media for post {post_id}: {e}")
                # Не прерываем выполнение, если медиа не загрузилось

        return result

    async def _update_item(
        self,
        posts_endpoint: PostsEndpoint,
        media_endpoint: MediaEndpoint,
        mapper: ContentMapper,
        content_processor: ContentProcessor,
        row: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Обновить существующую запись."""
        post_id = int(row["id"])

        # Dry-run: только логируем, не отправляем
        if self.config.sync.dry_run:
            taxonomy_ids, _ = await self._process_taxonomies(row, mapper)
            payload = mapper.map_row(row, taxonomy_ids)
            logger.info(f"[DRY-RUN] Would UPDATE post ID={post_id}: {payload.get('title', 'No title')}")
            return {"id": post_id, "link": "", "dry_run": True}

        # 1. Обрабатываем таксономии
        taxonomy_ids, primary_terms = await self._process_taxonomies(row, mapper)

        # 2. Создаем payload
        payload = mapper.map_row(row, taxonomy_ids)
        _cf = mapper.allowed_columns

        # 3. Убираем featured_media из payload если --skip-media
        if self.config.sync.skip_media:
            payload.pop("featured_media", None)

        # 4. Обрабатываем content_path если заполнен
        # Пропускаем если columns_filter активен и content не в фильтре
        if (not _cf or "content" in _cf) and has_value(row, "content_path"):
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

        # 5. Загружаем медиа или прикрепляем существующую картинку
        saved_media_result = None

        if not self.config.sync.skip_media:
            # Проверяем: нужно ли загружать новую картинку или прикрепить существующую
            # Пропускаем если columns_filter активен и img_path/featured_media не в фильтре
            _should_media = not _cf or "img_path" in _cf or "featured_media" in _cf
            img_path_filled = _should_media and mapper.should_upload_media(row)
            featured_media_in_excel = (
                _should_media and has_value(row, "featured_media") and str(row["featured_media"]).strip() != ""
            )

            if img_path_filled:
                # Загружаем новую картинку
                media_path = cast(str, mapper.extract_media_path(row))  # should_upload_media() его гарантирует
                # Берем title и alt_text из строки
                title = row.get("title", "") if has_value(row, "title") else ""
                alt_text = row.get("alt_text", "") if has_value(row, "alt_text") else ""
                # Если alt_text не заполнен, используем title
                if not alt_text:
                    alt_text = title
                caption = row.get("caption", "") if has_value(row, "caption") else ""

                try:
                    logger.debug(f"Uploading media for post {post_id}: {media_path}")
                    media_result = await self._upload_media(media_endpoint, media_path, title, alt_text, caption)

                    if media_result:
                        # Добавляем featured_media в payload для одновременного обновления
                        payload["featured_media"] = media_result["id"]
                        logger.debug(f"Added featured_media to payload: {media_result['id']}")
                        # Сохраняем media_result для последующего добавления в result
                        saved_media_result = media_result
                    else:
                        logger.warning("Media upload returned None, skipping featured_media update")
                        saved_media_result = None

                except Exception as e:
                    logger.warning(f"Failed to upload media for post {post_id}: {e}")
                    saved_media_result = None

            elif featured_media_in_excel and not self.config.sync.skip_existing_media:
                # img_path не заполнен, но featured_media есть в Excel - прикрепляем существующую картинку
                media_id = int(row["featured_media"])

                # Пропускаем если media_id = 0 (означает "нет картинки")
                if media_id > 0:
                    logger.debug(f"Attaching existing media ID={media_id} to post {post_id}")
                    payload["featured_media"] = media_id

                    # Получаем данные медиа для image_url
                    try:
                        media_data = await media_endpoint.get(media_id)
                        saved_media_result = {"id": media_id, "source_url": media_data.get("source_url")}
                        logger.debug(f"Got existing media data: ID={media_id}, URL={saved_media_result['source_url']}")
                    except Exception as e:
                        logger.warning(f"Failed to get media data for ID={media_id}: {e}")
                        saved_media_result = {"id": media_id, "source_url": ""}

        # 6. Обновляем запись
        has_content = "content" in payload
        content_len = len(payload["content"]) if has_content else 0
        content_note = f"yes ({content_len} chars)" if has_content else "no"
        logger.info(
            f"Sending update for post ID={post_id}: payload keys={list(payload.keys())}, content={content_note}"
        )
        result = await posts_endpoint.update(post_id, payload)

        # 6a. Основные (primary) термины таксономий — best-effort
        await self._apply_primary_terms(posts_endpoint, post_id, primary_terms)

        # 7. Проверяем что content реально обновился (сравниваем хеши текста без тегов)
        if has_content:
            import hashlib
            import re

            def _strip(s: str) -> str:
                return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", s))

            # md5 — сверка отправленного текста с сохранённым, не криптография.
            sent_hash = hashlib.md5(_strip(payload["content"]).encode(), usedforsecurity=False).hexdigest()[:8]

            response_content = result.get("content", {})
            if isinstance(response_content, dict):
                raw = response_content.get("raw") or response_content.get("rendered", "")
            else:
                raw = str(response_content) if response_content else ""

            if raw:
                recv_hash = hashlib.md5(_strip(raw).encode(), usedforsecurity=False).hexdigest()[:8]
                if sent_hash == recv_hash:
                    logger.info(f"Content CONFIRMED for post ID={post_id}: hash match ({sent_hash})")
                else:
                    logger.warning(
                        f"Content MISMATCH for post ID={post_id}: "
                        f"sent hash={sent_hash}, received hash={recv_hash}, "
                        f"sent {content_len} chars, received {len(raw)} chars"
                    )
            else:
                logger.warning(f"Content NOT CONFIRMED for post ID={post_id}: WordPress returned empty content")

        # 8. Добавляем media данные в result (если они были)
        if saved_media_result:
            result["featured_media"] = saved_media_result.get("id")
            result["image_url"] = saved_media_result.get("source_url")
            logger.info(
                f"Added media data to result: featured_media={result['featured_media']}, "
                f"image_url={result['image_url']}"
            )

        # 9. Получаем актуальные данные записи (link, modified могут отсутствовать в ответе update)
        if "link" not in result or "modified" not in result:
            try:
                fresh_data = await posts_endpoint.get(post_id)
                if "link" not in result:
                    result["link"] = fresh_data.get("link")
                if "modified" not in result:
                    result["modified"] = fresh_data.get("modified")
                logger.debug(
                    f"Fetched fresh data for post {post_id}: link={result.get('link')}, "
                    f"modified={result.get('modified')}"
                )
            except Exception as e:
                logger.warning(f"Failed to fetch updated data for post {post_id}: {e}")

        return result

    async def _delete_item(self, posts_endpoint: PostsEndpoint, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Удалить запись (в корзину)."""
        post_id = int(row["id"])

        # Dry-run: только логируем, не удаляем
        if self.config.sync.dry_run:
            logger.info(f"[DRY-RUN] Would DELETE post ID={post_id}")
            return {"id": post_id, "deleted": True, "dry_run": True}

        logger.debug(f"Deleting post ID={post_id}")

        # Удаляем featured media если включено
        if self.config.sync.delete_featured_media:
            try:
                # Получаем данные записи, чтобы узнать featured_media
                post_data = await posts_endpoint.get(post_id)
                featured_media_id = post_data.get("featured_media")

                if featured_media_id and featured_media_id > 0:
                    media_endpoint = self._make_media_endpoint()
                    await media_endpoint.delete(featured_media_id, force=True)
                    logger.info(f"Deleted featured media ID={featured_media_id} for post ID={post_id}")
            except Exception as e:
                logger.warning(f"Failed to delete featured media for post ID={post_id}: {e}")

        # Удаляем в корзину (force=False)
        result = await posts_endpoint.delete(post_id, force=False)
        return result

    async def _process_taxonomies(
        self, row: Mapping[str, Any], mapper: ContentMapper
    ) -> tuple[Dict[str, list[int]], Dict[str, int]]:
        """
        Обработать таксономии из записи.

        Уважает self.config.sync.dry_run: термины по-прежнему ищутся (это
        безопасно и нужно для точного превью), но не создаются — отсутствующий
        термин возвращается с ID=0 (см. TaxonomiesEndpoint.process_taxonomy_string).

        Args:
            row: Запись (Mapping поле → значение)
            mapper: ContentMapper

        Returns:
            (Dict {taxonomy_name: [term_ids]},
             Dict {taxonomy_name: primary_term_id} — первый термин строки)
        """
        # Если skip_tax=True, пропускаем обработку таксономий
        if self.config.sync.skip_tax:
            return {}, {}

        taxonomy_fields = mapper.detector.get_taxonomy_fields()

        # Если columns_filter активен — обрабатываем только таксономии из фильтра
        if mapper.allowed_columns:
            taxonomy_fields = [f for f in taxonomy_fields if f in mapper.allowed_columns]
            if not taxonomy_fields:
                return {}, {}

        taxonomy_ids = {}
        primary_terms: Dict[str, int] = {}

        for tax_field in taxonomy_fields:
            if tax_field not in row:
                continue

            value = row[tax_field]
            if is_blank(value):
                continue

            # Создаем endpoint для таксономии
            normalized_tax_field = normalize_taxonomy_name(tax_field)
            tax_endpoint = TaxonomiesEndpoint(self.wp_client, normalized_tax_field)

            # Обрабатываем строку с таксономиями (передаем кэш для обновления)
            # dry_run пробрасывается до create-веток get_or_create*: поиск
            # существующих терминов выполняется как обычно, но ничего не создаётся
            term_ids, primary_id = await tax_endpoint.process_taxonomy_string(
                str(value), taxonomy_cache=self.taxonomy_cache, dry_run=self.config.sync.dry_run
            )

            if term_ids:
                taxonomy_ids[tax_field] = term_ids

            if primary_id is not None:
                primary_terms[tax_field] = primary_id

        return taxonomy_ids, primary_terms

    async def _apply_primary_terms(
        self, posts_endpoint: PostsEndpoint, post_id: int, primary_terms: Dict[str, int]
    ) -> None:
        """
        Записать основные (primary) термины в мета-поля Yoast SEO.

        Best-effort: если мета-поле не зарегистрировано в REST или запрос упал —
        логируем предупреждение и продолжаем, привязка терминов к записи уже
        выполнена и от этого вызова не зависит.

        Вызывается только из не-dry-run веток _add_item/_update_item (dry-run
        уходит на return раньше) — под --dry-run реального PUT не будет.

        Args:
            posts_endpoint: PostsEndpoint
            post_id: ID записи
            primary_terms: {taxonomy_name: primary_term_id}
        """
        if not primary_terms:
            return

        meta = {primary_term_meta_key(taxonomy): term_id for taxonomy, term_id in primary_terms.items()}

        try:
            await posts_endpoint.update(post_id, {"meta": meta})
            logger.debug(f"Set primary terms for post ID={post_id}: {meta}")
        except Exception as e:
            logger.warning(f"Failed to set primary terms for post ID={post_id}: {e}")

    async def _upload_media(
        self, media_endpoint: MediaEndpoint, media_path: str, title: str = "", alt_text: str = "", caption: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Загрузить медиафайл.

        Args:
            media_endpoint: MediaEndpoint
            media_path: Путь к файлу (локальный или URL)
            title: Название для медиа
            alt_text: Alt текст для изображения
            caption: Подпись

        Returns:
            Результат загрузки или None
        """
        # Проверяем это URL или локальный путь
        if media_path.startswith("http://") or media_path.startswith("https://"):
            # Загрузка по URL
            return await media_endpoint.upload_from_url(
                media_path, title=title if title else None, alt_text=alt_text if alt_text else None
            )
        else:
            # Локальный файл
            from ..utils.path_utils import resolve_media_file

            file_path = resolve_media_file(media_path, self.config.paths.media_folder)

            if not file_path.exists():
                logger.warning(f"Media file not found: {media_path}")
                return None

            return await media_endpoint.upload_file(
                file_path,
                title=title if title else None,
                alt_text=alt_text if alt_text else None,
                caption=caption if caption else None,
            )
