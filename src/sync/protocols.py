"""
Protocol-интерфейсы для sync-классов (для тестирования и статической проверки типов)
"""

from typing import Optional, Protocol, Sequence

from ..models import SyncReport
from ..records.base import Record, RecordWriter


class ContentSyncProtocol(Protocol):
    """Протокол для синхронизации контента."""

    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
    ) -> SyncReport: ...


class TaxonomySyncProtocol(Protocol):
    """Протокол для синхронизации таксономий."""

    async def sync_records(
        self,
        discriminator: str,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
    ) -> SyncReport: ...


class MediaSyncProtocol(Protocol):
    """Протокол для синхронизации медиафайлов."""

    async def sync_media(
        self,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
    ) -> SyncReport: ...
