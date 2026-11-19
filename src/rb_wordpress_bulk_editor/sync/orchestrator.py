"""
Диспетчеризация записей по типам перед синхронизацией.

Лист Excel однороден (имя листа = post_type или таксономия), а таб JSON — нет:
каждая запись несёт свой `type`/`taxonomy`. Поэтому записи сначала группируются
по дискриминатору, и только потом уходят в content- или taxonomy-sync.
"""

from typing import List, Optional, Sequence

from loguru import logger

from ..models import SyncReport, SyncResult
from ..records.base import Record, RecordWriter, group_by_discriminator
from .base_sync import BaseSync
from .progress import Progress
from .protocols import ContentSyncProtocol, TaxonomySyncProtocol


def merge_reports(reports: Sequence[SyncReport]) -> SyncReport:
    """Сложить отчёты нескольких групп в один."""
    stats = {"total": 0, "added": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
    errors: List[SyncResult] = []

    for report in reports:
        for key, value in report.to_stats().items():
            stats[key] += value
        errors.extend(report.errors)

    return SyncReport.from_stats(stats, errors)


async def sync_record_groups(
    records: Sequence[Record],
    content_sync: ContentSyncProtocol,
    taxonomy_sync: TaxonomySyncProtocol,
    writer: Optional[RecordWriter] = None,
    progress: Optional[Progress] = None,
) -> SyncReport:
    """
    Сгруппировать записи по дискриминатору и синхронизировать каждую группу.

    Args:
        records: Записи одного набора данных (лист Excel / таб JSON)
        content_sync: Синхронизация контента
        taxonomy_sync: Синхронизация таксономий
        writer: Writer для обратной записи (если нужна)
        progress: Счётчик [n/total] набора (пропущенные засчитываются сразу)

    Returns:
        Суммарный SyncReport по всем группам
    """
    reports: List[SyncReport] = []
    if progress:
        progress.skipped(BaseSync._count_skipped(records))

    for group in group_by_discriminator(records):
        discriminator = group[0].discriminator
        is_taxonomy = group[0].is_taxonomy

        kind = "таксономия" if is_taxonomy else "контент"
        logger.info(f"Группа '{discriminator}' ({kind}): {len(group)} записей")

        sync = taxonomy_sync if is_taxonomy else content_sync
        reports.append(await sync.sync_records(discriminator, group, writer, progress))

    return merge_reports(reports)
