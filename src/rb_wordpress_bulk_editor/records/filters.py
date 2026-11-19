"""
Фильтрация записей по значениям полей (`--filter field=value`).

Предикат применяется к `Record.data` — после чтения источника, но до маппинга
в payload, поэтому работает одинаково для Excel и JSON. Ключ сравнивается как
есть, включая dot-нотацию (`acf.f1`, `seo.title`): в сыром виде это обычное
плоское имя поля и у колонки Excel, и у ключа записи JSON. Особый случай —
`type`/`taxonomy`: в `Record.data` их нет (источник считает их метаданными),
но фильтр понимает их как дискриминатор записи.

Сравнение строковое и точное — это инструмент сужения выборки, а не язык
запросов.
"""

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from ..exceptions import ValidationError
from .base import Record, is_blank

# Одно условие фильтра: (имя поля, ожидаемое значение)
RecordFilter = Tuple[str, str]

# Поля-дискриминаторы: источник убирает их из `Record.data` (они уже в
# discriminator/is_taxonomy), но в файле пользователь видит именно их —
# `--filter type=rb_services` обязан работать так, как написано в записи.
TYPE_FIELD = "type"
TAXONOMY_FIELD = "taxonomy"


def parse_filters(values: Optional[Iterable[str]]) -> List[RecordFilter]:
    """
    Разобрать список `field=value` из CLI.

    Raises:
        ValidationError: элемент без `=` или с пустым именем поля (exit 2).
    """
    filters: List[RecordFilter] = []

    for raw in values or []:
        field, sep, value = str(raw).partition("=")
        field = field.strip()
        if not sep or not field:
            raise ValidationError(
                f"Неверный формат --filter: '{raw}'. Ожидается field=value, например --filter type=rb_services"
            )
        filters.append((field, value.strip()))

    return filters


def _field_value(record: Record, field: str) -> Any:
    """Значение поля записи, с подстановкой дискриминатора для type/taxonomy."""
    if field in record.data:
        return record.data[field]
    if field == TYPE_FIELD and not record.is_taxonomy:
        return record.discriminator
    if field == TAXONOMY_FIELD and record.is_taxonomy:
        return record.discriminator
    return None


def matches(record: Record, filters: Sequence[RecordFilter]) -> bool:
    """Запись проходит все условия (объединение по И)."""
    for field, expected in filters:
        value = _field_value(record, field)
        actual = "" if is_blank(value) else str(value).strip()
        if actual != expected:
            return False
    return True
