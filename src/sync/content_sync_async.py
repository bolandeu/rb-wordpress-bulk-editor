"""
Асинхронная синхронизация контента с параллельной обработкой записей.
"""

from functools import partial
import asyncio
from typing import List, Optional, Sequence
from loguru import logger

from ..wordpress.taxonomies import TaxonomyCache
from ..mappers.content_mapper import ContentMapper
from ..config import AppConfig
from ..records.base import Record, RecordWriter, collect_fields
from ..wordpress.client import WordPressClient
from ..utils.content_processor import ContentProcessor
from ..utils.file_values import FileValueResolver
from ..utils.async_helpers import AsyncBatchProcessor, ProcessResult
from ..models import SyncReport, SyncResult
from .progress import Progress
from .content_sync_base import ContentSyncBase


class ContentSyncAsync(ContentSyncBase):
    """
    Асинхронная версия ContentSync с параллельной обработкой записей.

    Отличия от ContentSync:
    - Записи обрабатываются параллельно с ограничением Semaphore
    - Используется AsyncBatchProcessor для управления concurrency
    - Результаты собираются через asyncio.gather()
    """

    def __init__(
        self,
        wp_client: WordPressClient,
        config: AppConfig,
        taxonomy_cache: TaxonomyCache
    ):
        super().__init__(wp_client, config, taxonomy_cache)
        self.batch_processor = AsyncBatchProcessor(config.sync.concurrency)

    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
        progress: Optional[Progress] = None,
    ) -> SyncReport:
        """
        Асинхронная синхронизация группы записей с параллельной обработкой.

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

            logger.info(f"Processing {action}: {len(action_records)} rows (PARALLEL)")

            # Создаем задачи для параллельной обработки
            tasks = []
            for record in action_records:
                idx = record.write_key
                row = record.data

                # Валидация (делаем синхронно перед созданием задачи)
                is_valid, error_msg = mapper.validate_row(row, action)
                if not is_valid:
                    logger.warning(f"Row {idx}: Validation failed: {error_msg}")
                    stats["errors"] += 1
                    if progress:
                        progress.record_done(record, action, error=True)
                    continue

                # Функция обработки (partial для передачи в process_row)
                if action == "delete":
                    process_func = partial(
                        self._delete_item,
                        posts_endpoint,
                        row
                    )
                elif action == "update":
                    process_func = partial(
                        self._update_item,
                        posts_endpoint,
                        media_endpoint,
                        mapper,
                        content_processor,
                        row
                    )
                else:  # add
                    process_func = partial(
                        self._add_item,
                        posts_endpoint,
                        media_endpoint,
                        mapper,
                        content_processor,
                        row
                    )

                # Создаем задачу через AsyncBatchProcessor
                task = self._tracked(
                    self.batch_processor.process_row(action, idx, row, process_func),
                    record, action, progress,
                )
                tasks.append(task)

            # Выполняем ВСЕ задачи параллельно
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Собираем статистику
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Unexpected error during gather: {result}")
                        stats["errors"] += 1
                        continue

                    if not isinstance(result, ProcessResult):
                        logger.error(f"Unexpected result type: {type(result)}")
                        stats["errors"] += 1
                        continue

                    if result.success:
                        # Обновляем счетчики
                        if action == "add":
                            stats["added"] += 1
                        elif action == "update":
                            stats["updated"] += 1
                        elif action == "delete":
                            stats["deleted"] += 1

                        # Записываем в источник через ASYNC метод
                        if writer and result.api_response and self.config.sync.update_excel:
                            await writer.update_from_result_async(
                                result.row_index,
                                result.api_response,
                                action,
                                columns_filter=self.config.sync.columns_filter
                            )
                    else:
                        stats["errors"] += 1
                        error_results.append(SyncResult(
                            row_index=result.row_index,
                            action=result.action,
                            post_id=None,
                            success=False,
                            error=str(result.error) if result.error else "Неизвестная ошибка",
                        ))

        # Подсчитываем пропущенные
        stats["skipped"] = self._count_skipped(records)
        stats["total"] = len(records)

        return SyncReport.from_stats(stats, error_results)
