"""
Асинхронная синхронизация таксономий с параллельной обработкой строк.
"""

from functools import partial
import asyncio
from typing import Dict, List, Optional
import pandas as pd
from loguru import logger

from ..wordpress.client import WordPressClient
from ..wordpress.taxonomies import normalize_taxonomy_name
from ..excel.writer import ExcelWriter
from ..config import AppConfig
from ..utils.async_helpers import AsyncBatchProcessor, ProcessResult
from ..models import SyncReport, SyncResult
from .taxonomy_sync_base import TaxonomySyncBase


class TaxonomySyncAsync(TaxonomySyncBase):
    """
    Асинхронная версия TaxonomySync с параллельной обработкой строк.

    Отличия от TaxonomySync:
    - Строки обрабатываются параллельно с ограничением Semaphore
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

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> SyncReport:
        """
        Асинхронная синхронизация листа таксономии с параллельной обработкой строк.

        Args:
            sheet_name: Название листа (= название таксономии)
            df: DataFrame с данными листа
            excel_writer: Writer для обратной записи (если нужна)

        Returns:
            SyncReport со статистикой и списком ошибочных строк
        """
        stats = self._init_stats()
        error_results: List[SyncResult] = []

        # Нормализуем имя таксономии для API
        normalized_taxonomy = normalize_taxonomy_name(sheet_name)

        # Создаем endpoint
        taxonomy_endpoint = self._make_taxonomy_endpoint(normalized_taxonomy)

        # Обрабатываем в порядке: delete → update → add
        for action in ["delete", "update", "add"]:
            # Фильтруем строки по action
            action_rows = df[df["action"].str.lower().str.strip() == action]

            if len(action_rows) == 0:
                continue

            logger.info(f"Processing {action}: {len(action_rows)} rows (PARALLEL)")

            # ✨ Создаем задачи для параллельной обработки
            tasks = []
            for idx, row in action_rows.iterrows():
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
                task = self.batch_processor.process_row(
                    action, idx, row, process_func
                )
                tasks.append(task)

            # ✨ Выполняем ВСЕ задачи параллельно
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

                        # Записываем в Excel через ASYNC метод
                        if excel_writer and result.api_response and self.config.sync.update_excel:
                            await excel_writer.update_from_api_response_async(
                                sheet_name,
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
        valid_actions = {"add", "update", "delete"}
        skipped = df[~df["action"].str.lower().str.strip().isin(valid_actions)]
        stats["skipped"] = len(skipped)
        stats["total"] = len(df)

        return SyncReport.from_stats(stats, error_results)
