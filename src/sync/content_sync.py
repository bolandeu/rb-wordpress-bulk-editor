"""
Синхронизация контента (posts, pages, custom types) — последовательная обработка.
"""

from typing import List, Optional, Sequence
from loguru import logger

from ..mappers.content_mapper import ContentMapper
from ..records.base import Record, RecordWriter, collect_fields
from ..utils.content_processor import ContentProcessor
from ..utils.file_values import FileValueResolver
from ..models import SyncReport, SyncResult
from .progress import Progress
from .content_sync_base import ContentSyncBase


class ContentSync(ContentSyncBase):
    """Синхронизация контента — последовательная обработка записей."""

    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
        progress: Optional[Progress] = None,
    ) -> SyncReport:
        """
        Синхронизировать группу записей одного типа контента (последовательно).

        Args:
            discriminator: Тип контента (post_type)
            records: Записи одного типа
            writer: Writer для обратной записи (если нужна)

        Returns:
            SyncReport со статистикой и списком ошибочных записей
        """
        stats = self._init_stats()
        error_results: List[SyncResult] = []

        records = list(records)

        # Создаем endpoints
        posts_endpoint = self._make_posts_endpoint(discriminator)
        media_endpoint = self._make_media_endpoint()

        # Создаем mapper и content processor
        from ..config import known_taxonomies

        mapper = ContentMapper(
            collect_fields(records),
            allowed_columns=self.config.sync.columns_filter,
            known_taxonomies=known_taxonomies(self.config),
            file_resolver=FileValueResolver(self.config.paths.excel_file.parent),
        )
        content_processor = ContentProcessor(self.config.paths.content_folder)

        # Обрабатываем в порядке: delete → update → add
        by_action = self._group_by_action(records)
        for action in self.ACTION_ORDER:
            action_records = by_action[action]

            if not action_records:
                continue

            logger.info(f"Processing {action}: {len(action_records)} rows")

            for record in action_records:
                idx = record.write_key
                row = record.data
                try:
                    # Валидация
                    is_valid, error_msg = mapper.validate_row(row, action)
                    if not is_valid:
                        logger.warning(f"Row {idx}: Validation failed: {error_msg}")
                        stats["errors"] += 1
                        if progress:
                            progress.record_done(record, action, error=True)
                        continue

                    # Обработка в зависимости от action
                    if action == "delete":
                        result = await self._delete_item(posts_endpoint, row)
                    elif action == "update":
                        result = await self._update_item(posts_endpoint, media_endpoint, mapper, content_processor, row)
                    else:  # add
                        result = await self._add_item(posts_endpoint, media_endpoint, mapper, content_processor, row)

                    # Обновляем статистику
                    if action == "add":
                        stats["added"] += 1
                    elif action == "update":
                        stats["updated"] += 1
                    elif action == "delete":
                        stats["deleted"] += 1

                    # Записываем обратно в источник
                    if writer and result and self.config.sync.update_excel:
                        writer.update_from_result(idx, result, action, columns_filter=self.config.sync.columns_filter)

                    if progress:
                        progress.record_done(record, action, result)

                except Exception as e:
                    logger.error(f"Row {idx}: Error during {action}: {e}")
                    stats["errors"] += 1
                    if progress:
                        progress.record_done(record, action, error=True)
                    error_results.append(
                        SyncResult(
                            row_index=idx,
                            action=action,
                            post_id=None,
                            success=False,
                            error=str(e),
                        )
                    )

        # Подсчитываем пропущенные
        stats["skipped"] = self._count_skipped(records)
        stats["total"] = len(records)

        return SyncReport.from_stats(stats, error_results)
