"""
Структурированные типы результатов для sync-операций
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..records.base import WriteKey


@dataclass
class SyncResult:
    """Результат обработки одной записи источника."""
    row_index: WriteKey
    action: str
    post_id: Optional[int]
    success: bool
    error: Optional[str] = None


@dataclass
class SyncReport:
    """Сводный отчёт по sync-операции для одного листа."""
    total: int
    created: int
    updated: int
    deleted: int
    skipped: int
    failed: int
    errors: List[SyncResult] = field(default_factory=list)

    @classmethod
    def from_stats(
        cls,
        stats: Dict[str, int],
        errors: Optional[List[SyncResult]] = None,
    ) -> "SyncReport":
        """Создать из существующего stats-словаря (обратная совместимость)."""
        return cls(
            total=stats.get("total", 0),
            created=stats.get("added", 0),
            updated=stats.get("updated", 0),
            deleted=stats.get("deleted", 0),
            skipped=stats.get("skipped", 0),
            failed=stats.get("errors", 0),
            errors=errors or [],
        )

    def to_stats(self) -> Dict[str, int]:
        """Конвертировать в словарь для совместимости с CLI-выводом."""
        return {
            "total": self.total,
            "added": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "errors": self.failed,
        }
