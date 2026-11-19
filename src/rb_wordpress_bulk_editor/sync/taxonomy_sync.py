"""
Синхронизация таксономий (categories, tags, custom taxonomies) — последовательная обработка.
"""

from typing import List, Optional, Sequence
from loguru import logger

from ..wordpress.taxonomies import normalize_taxonomy_name
from ..records.base import Record, RecordWriter
from ..models import SyncReport, SyncResult
from .progress import Progress
from .taxonomy_sync_base import TaxonomySyncBase


class TaxonomySync(TaxonomySyncBase):
    """Синхронизация таксономий — последовательная обработка записей."""

    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
        progress: Optional[Progress] = None,
    ) -> SyncReport:
        """
        Синхронизировать группу терминов одной таксономии (последовательно).

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

            logger.info(f"Processing {action}: {len(action_records)} rows")

            for record in action_records:
                idx = record.write_key
                row = record.data
                try:
                    # Обработка в зависимости от action
                    if action == "delete":
                        result = await self._delete_term(taxonomy_endpoint, row)
                    elif action == "update":
                        result = await self._update_term(taxonomy_endpoint, row)
                    else:  # add
                        result = await self._add_term(taxonomy_endpoint, row)

                    # Обновляем статистику
                    if action == "add":
                        stats["added"] += 1
                    elif action == "update":
                        stats["updated"] += 1
                    elif action == "delete":
                        stats["deleted"] += 1

                    # Записываем обратно в источник
                    if writer and result and self.config.sync.update_excel:
                        writer.update_from_result(idx, result, action)

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
