"""
Асинхронная синхронизация таксономий с параллельной обработкой записей.
"""

from functools import partial
import asyncio
from typing import List, Optional, Sequence
from loguru import logger

from ..wordpress.client import WordPressClient
from ..wordpress.taxonomies import normalize_taxonomy_name
from ..config import AppConfig
from ..records.base import Record, RecordWriter
from ..utils.async_helpers import AsyncBatchProcessor, ProcessResult
from ..models import SyncReport, SyncResult
from .progress import Progress
from .taxonomy_sync_base import TaxonomySyncBase


class TaxonomySyncAsync(TaxonomySyncBase):
    """
    Асинхронная версия TaxonomySync с параллельной обработкой записей.

    Отличия от TaxonomySync:
    - Записи обрабатываются параллельно с ограничением Semaphore
    - Используется AsyncBatchProcessor для управления concurrency
    - Результаты собираются через asyncio.gather()
    """

    def __init__(
        self,
        wp_client: WordPressClient,
        config: AppConfig
    ):
        super().__init__(wp_client, config)
        self.batch_processor = AsyncBatchProcessor(config.sync.concurrency)

    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
        progress: Optional[Progress] = None,
    ) -> SyncReport:
        """
        Асинхронная синхронизация группы терминов с параллельной обработкой.

        Args:
            discriminator: Название таксономии
            records: Записи-термины одной таксономии
            writer: Writer для обратной записи (если нужна)

        Returns:
            SyncReport со статистикой и списком ошибочных записей
        """
        stats = self._init_stats()
        error_results: List[SyncResult] = []

        records = list(records)

        # Нормализуем имя таксономии для API
        normalized_taxonomy = normalize_taxonomy_name(discriminator)

        # Создаем endpoint
        taxonomy_endpoint = self._make_taxonomy_endpoint(normalized_taxonomy)

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

                # Функция обработки (partial для передачи в process_row)
                if action == "delete":
                    process_func = partial(
                        self._delete_term,
                        taxonomy_endpoint,
                        row
                    )
                elif action == "update":
                    process_func = partial(
                        self._update_term,
                        taxonomy_endpoint,
                        row
                    )
                else:  # add
                    process_func = partial(
                        self._add_term,
                        taxonomy_endpoint,
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
                                action
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
