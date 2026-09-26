"""
Проверка JSON-таба без обращения к сайту (команда `validate`).

Структура — те же правила, что у `JsonRecordSource` (`type`/`taxonomy` ровно
одно из двух, `_row_num` уникален), но собираются все ошибки сразу. Плюс
значения-пути к файлам (`acf.more = "content-write/1.html"`) должны существовать.

Битая ссылка на файл у записи, которую `sync` отправит (`add`/`update`), —
ошибка; у остальных (`skip`, пусто) — предупреждение: на сайт она не уйдёт.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..constants import SYSTEM_COLUMNS, VALID_ACTIONS, normalize_action
from ..utils.file_values import FileValueResolver
from .json_source import ROW_NUM_FIELD, SOURCE_META_FIELDS, JsonRecordSource

# Действия, при которых значения записи уходят в WordPress
_SENDING_ACTIONS = {"add", "update"}


@dataclass
class TabCheck:
    """Итог проверки одного таба."""
    tab: str
    records: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"tab": self.tab, "records": self.records, "errors": self.errors, "warnings": self.warnings}


def check_tab(path: Path, tab: str) -> TabCheck:
    """Проверить таб целиком; исключений на битых записях не бросает."""
    path = Path(path)
    source = JsonRecordSource(path, tab)
    raw = source.raw_records()
    result = TabCheck(tab=tab, records=len(raw), errors=source.problems())
    resolver = FileValueResolver(path.parent)

    for position, record in enumerate(raw):
        if not isinstance(record, dict):
            continue  # уже в errors из problems()
        where = f"Таб '{tab}', запись {record.get(ROW_NUM_FIELD, f'#{position}')}"
        raw_action = record.get("action")
        action = normalize_action(raw_action)
        if action and action not in VALID_ACTIONS:
            result.warnings.append(f"{where}: неизвестный action {raw_action!r} — sync пропустит запись")

        for name, value in record.items():
            if name in SOURCE_META_FIELDS or name in SYSTEM_COLUMNS:
                continue
            problem = resolver.check(name, value)
            if not problem:
                continue
            if action in _SENDING_ACTIONS:
                result.errors.append(f"{where}: {problem}")
            else:
                result.warnings.append(f"{where}: {problem} (action={action or 'пусто'} — на сайт не уйдёт)")

    return result
