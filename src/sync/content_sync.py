"""
Синхронизация контента (posts, pages, custom types) — последовательная обработка.
"""

from typing import Dict, List, Optional
import pandas as pd
from loguru import logger

from ..excel.writer import ExcelWriter
from ..mappers.content_mapper import ContentMapper
from ..utils.content_processor import ContentProcessor
from ..models import SyncReport, SyncResult
from .content_sync_base import ContentSyncBase


class ContentSync(ContentSyncBase):
    """Синхронизация контента — последовательная обработка строк."""

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> SyncReport:
        """
        Синхронизировать один лист Excel (последовательно).

        Args:
            sheet_name: Название листа (= тип контента)
            df: DataFrame с данными листа
            excel_writer: Writer для обратной записи (если нужна)

        Returns:
            SyncReport со статистикой и списком ошибочных строк
        """
        stats = self._init_stats()
        error_results: List[SyncResult] = []

        # Создаем endpoints
        posts_endpoint = self._make_posts_endpoint(sheet_name)
        media_endpoint = self._make_media_endpoint()

        # Создаем mapper и content processor
        mapper = ContentMapper(df.columns.tolist(), allowed_columns=self.config.sync.columns_filter)
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
                            action,
                            columns_filter=self.config.sync.columns_filter
                        )

                except Exception as e:
                    logger.error(f"Row {idx}: Error during {action}: {e}")
                    stats["errors"] += 1
                    error_results.append(SyncResult(
                        row_index=idx,
                        action=action,
                        post_id=None,
                        success=False,
                        error=str(e),
                    ))

        # Подсчитываем пропущенные
        valid_actions = {"add", "update", "delete"}
        skipped = df[~df["action"].str.lower().str.strip().isin(valid_actions)]
        stats["skipped"] = len(skipped)
        stats["total"] = len(df)

        return SyncReport.from_stats(stats, error_results)
