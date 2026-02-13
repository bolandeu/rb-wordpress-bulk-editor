"""
Синхронизация таксономий (categories, tags, custom taxonomies) — последовательная обработка.
"""

from typing import Dict, Optional
import pandas as pd
from loguru import logger

from ..wordpress.taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name
from ..excel.writer import ExcelWriter
from .taxonomy_sync_base import TaxonomySyncBase


class TaxonomySync(TaxonomySyncBase):
    """Синхронизация таксономий — последовательная обработка строк."""

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> Dict[str, int]:
        """
        Синхронизировать один лист таксономии (последовательно).

        Args:
            sheet_name: Название листа (= название таксономии)
            df: DataFrame с данными листа
            excel_writer: Writer для обратной записи (если нужна)

        Returns:
            Статистика: {added: N, updated: N, deleted: N, errors: N, skipped: N, total: N}
        """
        stats = self._init_stats()

        # Нормализуем имя таксономии для API
        normalized_taxonomy = normalize_taxonomy_name(sheet_name)

        # Создаем endpoint
        taxonomy_endpoint = TaxonomiesEndpoint(self.wp_client, normalized_taxonomy)

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

        # Подсчитываем пропущенные
        valid_actions = {"add", "update", "delete"}
        skipped = df[~df["action"].str.lower().str.strip().isin(valid_actions)]
        stats["skipped"] = len(skipped)
        stats["total"] = len(df)

        return stats
