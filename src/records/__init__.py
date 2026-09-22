"""
Абстракция источника записей: Excel и JSON поверх одного sync-движка.

Здесь реэкспортируются только базовые типы (без pandas/Excel-зависимостей).
Конкретные реализации импортируются напрямую:
`from ..records.excel import ExcelRecordSource`,
`from ..records.json_source import JsonRecordSource`.
"""

from .base import (
    Record,
    RecordSource,
    RecordWriter,
    WriteKey,
    collect_fields,
    group_by_discriminator,
    has_value,
    is_blank,
    is_missing,
)

__all__ = [
    "Record",
    "RecordSource",
    "RecordWriter",
    "WriteKey",
    "collect_fields",
    "group_by_discriminator",
    "has_value",
    "is_blank",
    "is_missing",
]
