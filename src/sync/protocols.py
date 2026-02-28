"""
Protocol-интерфейсы для sync-классов (для тестирования и статической проверки типов)
"""

from typing import Optional, Protocol

import pandas as pd

from ..excel.writer import ExcelWriter
from ..models import SyncReport


class ContentSyncProtocol(Protocol):
    """Протокол для синхронизации контента."""

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None,
    ) -> SyncReport: ...


class TaxonomySyncProtocol(Protocol):
    """Протокол для синхронизации таксономий."""

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None,
    ) -> SyncReport: ...


class MediaSyncProtocol(Protocol):
    """Протокол для синхронизации медиафайлов."""

    async def sync_media(
        self,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None,
    ) -> SyncReport: ...
