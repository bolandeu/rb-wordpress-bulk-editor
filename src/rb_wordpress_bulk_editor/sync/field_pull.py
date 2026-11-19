"""
Подтянуть отдельные поля из WordPress в файл данных по id записи (команда `update`).

WordPress только читается. Формат файла (Excel/JSON) движку не важен: записи
приходят через `RecordSource`, изменения уходят через `RecordWriter.update_record`.
Контент и термины в одном наборе различаются по `Record.is_taxonomy` — для
терминов запрос идёт в endpoint таксономии, для контента — в endpoint типа записи.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from ..constants import is_dot_notation_column, read_nested_value
from ..exceptions import NotFoundError
from ..records.base import Record, is_blank
from ..wordpress.posts import PostsEndpoint
from ..wordpress.taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name

# Сколько записей набора обрабатывает --test-mode
TEST_MODE_LIMIT = 3


@dataclass
class PullTarget:
    """Один набор данных (лист Excel / таб JSON) для подтягивания полей.

    Attributes:
        name: Имя листа или таба.
        records: Записи набора.
        writer: Куда писать (None — dry-run: writer не открывается вовсе).
        known_fields: Поля, которые можно записать (колонки листа Excel);
            None — любые (JSON: у записи нет фиксированной шапки).
    """

    name: str
    records: List[Record]
    writer: Optional[Any] = None
    known_fields: Optional[Set[str]] = None


@dataclass
class PullReport:
    """Итог прогона."""

    found: int = 0
    updated: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "updated": self.updated,
            "items": self.items,
            "warnings": self.warnings,
        }


def normalize_id(value: Any) -> Optional[int]:
    """id записи из файла → int (Excel отдаёт `123.0`, JSON — `"123"`/`123`)."""
    if is_blank(value):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def extract_field_value(api_response: Dict[str, Any], column: str) -> Optional[Any]:
    """
    Значение одного поля записи из ответа WordPress API (None — поля в ответе нет).

    Поддерживает обычные поля, rendered/raw, excerpt (без HTML),
    ACF в legacy-виде (`acf_price`) и dot-нотацию (`seo.title`, `acf.f1`).
    """
    from ..excel.writer import normalize_api_value

    if is_dot_notation_column(column):
        return read_nested_value(column, api_response)

    if column.startswith("acf_"):
        acf = api_response.get("acf", {})
        if isinstance(acf, dict):
            value = acf.get(column[4:])
            return normalize_api_value(value) if value is not None else None
        return None

    if column == "excerpt":
        raw = normalize_api_value(api_response.get("excerpt"))
        if raw:
            raw = re.sub(r"<[^>]+>", "", str(raw)).strip()
        return raw

    if column in ("title", "content", "name", "description"):
        if column in api_response:
            return normalize_api_value(api_response[column])
        return None

    if column in api_response:
        value = api_response[column]
        return normalize_api_value(value) if isinstance(value, dict) else value

    return None


class _Endpoints:
    """Кэш endpoint'ов по (дискриминатор, термин ли)."""

    def __init__(self, client):
        self.client = client
        self._cache: Dict[tuple, Any] = {}

    def for_record(self, record: Record):
        key = (record.discriminator, record.is_taxonomy)
        if key not in self._cache:
            if record.is_taxonomy:
                taxonomy = normalize_taxonomy_name(record.discriminator)
                self._cache[key] = TaxonomiesEndpoint(self.client, taxonomy)
            else:
                self._cache[key] = PostsEndpoint(self.client, record.discriminator)
        return self._cache[key]


async def pull_fields(
    client,
    targets: List[PullTarget],
    columns: List[str],
    id_filter: Optional[Set[int]] = None,
    test_mode: bool = False,
    report: Optional[PullReport] = None,
) -> PullReport:
    """
    Подтянуть `columns` из WordPress в записи `targets` по их id.

    Args:
        client: Открытый WordPressClient.
        targets: Наборы данных; у каждого свой writer (None — dry-run).
        columns: Поля для подтягивания (dot-нотация — обычное имя поля).
        id_filter: Только эти id; None — все записи с заполненным id.
        test_mode: Только первые TEST_MODE_LIMIT записей каждого набора.
        report: Отчёт для накопления между вызовами (None — новый).

    Returns:
        PullReport; в `items` — что записано (или было бы записано в dry-run).
    """
    report = report if report is not None else PullReport()
    endpoints = _Endpoints(client)

    for target in targets:
        selected = []
        for record in target.records:
            record_id = normalize_id(record.data.get("id"))
            if record_id is None:
                continue
            if id_filter is not None and record_id not in id_filter:
                continue
            selected.append((record, record_id))
        if test_mode:
            selected = selected[:TEST_MODE_LIMIT]

        writable = [c for c in columns if target.known_fields is None or c in target.known_fields]
        absent = [c for c in columns if c not in writable]
        if absent and selected:
            report.warnings.append(f"'{target.name}': нет колонок {', '.join(absent)} — пропущены")

        for record, record_id in selected:
            report.found += 1
            try:
                api_response = await endpoints.for_record(record).get(record_id)
            except NotFoundError:
                report.warnings.append(f"'{target.name}': id={record_id} ({record.discriminator}) не найден (404)")
                continue

            update_data: Dict[str, Any] = {}
            for column in writable:
                value = extract_field_value(api_response, column)
                if value is not None:
                    update_data[column] = value
            if not update_data:
                report.warnings.append(f"'{target.name}': id={record_id} — запрошенных полей нет в ответе API")
                continue

            if target.writer is not None:
                target.writer.update_record(record.write_key, update_data)
            logger.debug(f"'{target.name}' id={record_id}: {list(update_data)}")
            report.updated += 1
            report.items.append(
                {
                    "dataset": target.name,
                    "id": record_id,
                    "type": record.discriminator,
                    "is_taxonomy": record.is_taxonomy,
                    "fields": update_data,
                }
            )

    return report
