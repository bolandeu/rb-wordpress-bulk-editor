"""
Сборка JSON-таба из данных WordPress (команда `fetch` в JSON) — аналог ExcelCreator.

Модель проекта antagor: один таб на сайт, записи разнородные. Термин отличается
от контента только колонками — заполнен `taxonomy` (имя таксономии), имя термина
в `name`, текст в `description`; у контента заполнен `type` (REST-имя типа:
`posts`, `pages`, `rb_services` — ровно то, что потом примет `sync`). Общие поля
(`id`, `action`, `slug`, `parent`, `acf.*`, `seo.*`) пишутся в одни и те же ключи.

ACF и SEO пишутся dot-нотацией (`acf.f1`, `seo.robots.index`), как в файлах
проекта, а не legacy-колонками `acf_*` из Excel-выгрузки.
"""

from typing import Any, Dict, Iterable, List, Tuple

from ..mappers.response_mapper import ResponseMapper
from .json_source import ROW_NUM_FIELD, TAXONOMY_FIELD, TYPE_FIELD

# Вложенные объекты ответа WordPress, которые разворачиваются в dot-нотацию
FLATTENED_OBJECTS = ("acf", "seo")

# Поддеревья, которые не выгружаются: разметка schema.org (JSON-LD), которую
# SEO-плагин генерирует сам, — не редактируемое поле, а ~12% объёма выгрузки.
SKIPPED_PREFIXES = ("seo.schema",)


def _is_tabular(value: Any) -> bool:
    """Значение укладывается в ячейку таблицы проекта.

    Списки объектов (повторители ACF, JSON-LD) — нет: в модели проекта
    (Google-таблица → JSON) их не редактируют, и пересобрать их обратно нельзя.
    """
    return not (isinstance(value, list) and any(isinstance(item, dict) for item in value))


# Поля, которые ResponseMapper добавляет любому типу записи: оставляем их только
# там, где тип их действительно поддерживает (есть в ответе API).
OPTIONAL_TAXONOMY_FIELDS = ("categories", "tags")

# Первый `_row_num` таба: как номер первой строки данных под шапкой таблицы
FIRST_ROW_NUM = 2


def flatten(value: Any, prefix: str) -> Dict[str, Any]:
    """`{"robots": {"index": true}}` с префиксом `seo` → `{"seo.robots.index": true}`.

    Списки и скаляры — листья: JSON хранит их как есть.
    """
    if not isinstance(value, dict):
        return {prefix: value}
    flat: Dict[str, Any] = {}
    for key, inner in value.items():
        flat.update(flatten(inner, f"{prefix}.{key}"))
    return flat


def _with_flattened_objects(row: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """Заменить legacy `acf_*` на dot-нотацию и развернуть acf/seo из ответа API."""
    row = {k: v for k, v in row.items() if not k.startswith("acf_")}
    for name in FLATTENED_OBJECTS:
        obj = item.get(name)
        if isinstance(obj, dict):
            row.update(
                {
                    key: value
                    for key, value in flatten(obj, name).items()
                    if not key.startswith(SKIPPED_PREFIXES) and _is_tabular(value)
                }
            )
    return row


def post_to_record(mapper: ResponseMapper, post: Dict[str, Any], post_type: str) -> Dict[str, Any]:
    """Запись контента. `type` — REST-имя типа, а не поле `type` ответа (`post`/`page`)."""
    row = mapper.map_post_to_excel(post)
    row.pop("content_path", None)  # ограничение ячейки Excel, в JSON контент целиком
    for name in OPTIONAL_TAXONOMY_FIELDS:
        if name not in post:
            row.pop(name, None)
    row = _with_flattened_objects(row, post)
    row[TYPE_FIELD] = post_type
    return row


def term_to_record(term: Dict[str, Any], taxonomy: str) -> Dict[str, Any]:
    """Запись термина: `taxonomy` заполнен, `type` отсутствует."""
    row = ResponseMapper.map_term_to_excel(term)
    row = _with_flattened_objects(row, term)
    row.pop(TYPE_FIELD, None)
    row["parent"] = term.get("parent", "")
    row[TAXONOMY_FIELD] = taxonomy
    return row


def _ordered(record: Dict[str, Any], row_num: int) -> Dict[str, Any]:
    """Служебные поля — первыми: `_row_num`, `action`, `id`, `type`/`taxonomy`."""
    head = {ROW_NUM_FIELD: row_num}
    for key in ("action", "id", TYPE_FIELD, TAXONOMY_FIELD):
        if key in record:
            head[key] = record[key]
    return {**head, **{k: v for k, v in record.items() if k not in head}}


def build_site_tab(
    mapper: ResponseMapper,
    terms: Iterable[Tuple[str, List[Dict[str, Any]]]],
    posts: Iterable[Tuple[str, List[Dict[str, Any]]]],
) -> List[Dict[str, Any]]:
    """
    Собрать записи таба сайта: сначала термины, затем контент.

    Термины первыми, потому что `sync` обрабатывает группы в порядке первого
    появления: повторная синхронизация таба создаст термины раньше записей,
    которые на них ссылаются. `_row_num` — сквозная нумерация с FIRST_ROW_NUM.

    Args:
        mapper: ResponseMapper с кэшем таксономий (id терминов → имена в контенте).
        terms: Пары (таксономия, термины из API).
        posts: Пары (REST-имя типа, записи из API).
    """
    records = [term_to_record(term, taxonomy) for taxonomy, items in terms for term in items]
    records += [post_to_record(mapper, post, post_type) for post_type, items in posts for post in items]
    return [_ordered(record, FIRST_ROW_NUM + i) for i, record in enumerate(records)]
