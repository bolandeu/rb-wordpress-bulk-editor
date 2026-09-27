"""
Проверка JSON-таба без обращения к сайту (команда `validate`).

Структура — те же правила, что у `JsonRecordSource` (`type`/`taxonomy` ровно
одно из двух, `_row_num` уникален), но собираются все ошибки сразу. Записи
с целой структурой дальше проходят общую проверку перед отправкой
(`preflight.check_records`): имена полей и таксономий, значения, файлы.
"""

from pathlib import Path
from typing import List, Optional

from .base import Record, is_blank
from .json_source import (
    SOURCE_META_FIELDS,
    TAXONOMY_FIELD,
    TYPE_FIELD,
    JsonRecordSource,
    _discriminator_problem,
    _write_key_for,
)
from .preflight import FieldRules, TabCheck, check_records


def _structurally_valid_records(source: JsonRecordSource) -> List[Record]:
    """Записи, которые удалось разобрать: битые уже попали в `problems()`."""
    records = []
    for position, raw in enumerate(source.raw_records()):
        if not isinstance(raw, dict) or _discriminator_problem(raw):
            continue
        taxonomy = raw.get(TAXONOMY_FIELD)
        is_taxonomy = not is_blank(taxonomy)
        records.append(
            Record(
                discriminator=str(taxonomy if is_taxonomy else raw.get(TYPE_FIELD)).strip(),
                is_taxonomy=is_taxonomy,
                data={k: v for k, v in raw.items() if k not in SOURCE_META_FIELDS},
                write_key=_write_key_for(raw, position),
            )
        )
    return records


def check_tab(path: Path, tab: str, rules: Optional[FieldRules] = None) -> TabCheck:
    """Проверить таб целиком; исключений на битых записях не бросает."""
    path = Path(path)
    source = JsonRecordSource(path, tab)
    records = _structurally_valid_records(source)
    result = check_records(
        tab,
        records,
        rules or FieldRules.from_settings(),
        base_dir=path.parent,
        where=source.where,
    )
    result.records = len(source.raw_records())
    result.errors[:0] = source.problems()
    return result
