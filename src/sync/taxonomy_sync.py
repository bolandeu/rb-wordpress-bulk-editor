"""
Синхронизация таксономий (categories, tags, custom taxonomies) — последовательная обработка.
"""

from typing import Dict, List, Optional
import pandas as pd
from loguru import logger

from ..wordpress.taxonomies import normalize_taxonomy_name
from ..excel.writer import ExcelWriter
from ..models import SyncReport, SyncResult
from .taxonomy_sync_base import TaxonomySyncBase


class TaxonomySync(TaxonomySyncBase):
    """Синхронизация таксономий — последовательная обработка строк."""

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> SyncReport:
        """
        Синхронизировать один лист таксономии (последовательно).

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

            logger.info(f"Processing {action}: {len(action_rows)} rows")

            for idx, row in action_rows.iterrows():
                try:
                    # Обработка в зависимости от action
                    if action == "delete":
                        result = await self._delete_term(
                            taxonomy_endpoint,
                            row
                        )
                    elif action == "update":
                        result = await self._update_term(
                            taxonomy_endpoint,
                            row
                        )
                    else:  # add
                        result = await self._add_term(
                            taxonomy_endpoint,
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
