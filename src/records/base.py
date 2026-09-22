"""
Базовые типы источников записей: запись, протоколы source/writer, группировка.

Модуль намеренно не знает ни про pandas, ни про Excel/JSON — только про
«запись как Mapping» и её ключ для обратной записи.
"""

from typing import Any, Dict, Iterable, Iterator, List, Mapping, NamedTuple, Optional, Protocol, Union


# Ключ обратной записи: label строки pandas (Excel) или _row_num/позиция (JSON)
WriteKey = Union[int, str]


class Record(NamedTuple):
    """Одна запись источника.

    Attributes:
        discriminator: Тип контента (post_type) или slug таксономии.
        is_taxonomy: True — запись описывает термин таксономии, False — контент.
        data: Поля записи (Mapping: `in`, `.get()`, `.keys()`).
        write_key: Ключ, по которому writer найдёт ту же запись обратно.
    """
    discriminator: str
    is_taxonomy: bool
    data: Mapping[str, Any]
    write_key: WriteKey


class RecordSource(Protocol):
    """Источник записей одного набора данных (лист Excel / таб JSON)."""

    def fields(self) -> List[str]: ...

    def read(self) -> Iterator[Record]: ...


class RecordWriter(Protocol):
    """Обратная запись результатов синхронизации в источник."""

    def update_from_result(
        self,
        write_key: WriteKey,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None,
    ) -> None: ...

    async def update_from_result_async(
        self,
        write_key: WriteKey,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None,
    ) -> None: ...

    def save(self) -> None: ...


def is_missing(value: Any) -> bool:
    """Значение отсутствует: None или NaN/NaT/NA из pandas.

    Аналог `pd.isna(value)` без pandas. Пустая строка пропуском НЕ считается
    (Excel после `fillna("")` и JSON с `""` ведут себя одинаково).
    """
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN/NaT не равны сами себе
    except TypeError:
        # pandas NA: сравнение неоднозначно — считаем пропуском
        return True


def is_blank(value: Any) -> bool:
    """Значение пустое: None, NaN/NaT/NA из pandas или пустая строка.

    Повторяет семантику `pd.isna(value) or value == ""`.
    """
    return is_missing(value) or value == ""


def has_value(row: Mapping[str, Any], field: str) -> bool:
    """Поле присутствует в записи и заполнено."""
    return field in row and not is_blank(row.get(field))


def collect_fields(records: Iterable[Record]) -> List[str]:
    """Объединение полей записей с сохранением порядка первого появления.

    Для Excel совпадает со списком колонок листа (все строки однородны),
    для JSON — набор ключей всех записей таба.
    """
    fields: List[str] = []
    seen = set()
    for record in records:
        for key in record.data.keys():
            if key not in seen:
                seen.add(key)
                fields.append(key)
    return fields


def group_by_discriminator(records: Iterable[Record]) -> List[List[Record]]:
    """Сгруппировать записи по (discriminator, is_taxonomy).

    Порядок групп — по первому появлению дискриминатора, порядок записей
    внутри группы сохраняется. Нужно, потому что JSON-таб (в отличие от листа
    Excel) содержит записи разных типов вперемешку.
    """
    groups: Dict[tuple, List[Record]] = {}
    for record in records:
        groups.setdefault((record.discriminator, record.is_taxonomy), []).append(record)
    return list(groups.values())
