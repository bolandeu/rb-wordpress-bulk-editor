"""
JSON как источник записей.

Формат файла:

    {
      "project": "...",
      "tabs": {
        "options":  [ {"_row_num": 2, "key": "...", "value": "..."} ],
        "site.ru":  [ {"_row_num": 2, "action": "add", "type": "rb_services", ...} ]
      }
    }

Таб — аналог листа Excel, но записи внутри него разнородные: `type` означает
контент, `taxonomy` — определение термина таксономии (имя берётся из `name`).
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from loguru import logger

from ..exceptions import RecordSourceError
from ..mappers.writeback_mapper import build_update_data_from_api
from .base import Record, WriteKey, collect_fields, is_blank


# Служебный ключ идентичности записи (аналог label строки pandas в Excel)
ROW_NUM_FIELD = "_row_num"

# Дискриминаторы записи — взаимоисключающие
TYPE_FIELD = "type"
TAXONOMY_FIELD = "taxonomy"

# Метаданные источника: в Record они уже есть отдельными полями,
# в данных записи (и тем более в payload WordPress) им делать нечего.
SOURCE_META_FIELDS = frozenset({ROW_NUM_FIELD, TYPE_FIELD, TAXONOMY_FIELD})

# Поля-идентификаторы: дописываются в запись, даже если их там не было
# (без этого потерялся бы `id` только что созданной записи WordPress).
NEW_FIELDS_ALWAYS = frozenset({
    "id", "action", "date", "modified", "link", "slug", "featured_media", "image_url",
})


def _load_tabs(path: Path) -> Dict[str, Any]:
    """Прочитать JSON-файл и вернуть его содержимое целиком."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RecordSourceError(f"Не удалось прочитать JSON-файл {path}: {e}") from e

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RecordSourceError(f"Некорректный JSON в файле {path}: {e}") from e

    if not isinstance(document, dict):
        raise RecordSourceError(f"Ожидался объект на верхнем уровне {path}, получен {type(document).__name__}")

    return document


def _get_tab(document: Dict[str, Any], path: Path, tab_name: str) -> List[Dict[str, Any]]:
    """Достать массив записей одного таба."""
    tabs = document.get("tabs")
    if not isinstance(tabs, dict):
        raise RecordSourceError(f"В файле {path} отсутствует объект 'tabs'")

    if tab_name not in tabs:
        available = ", ".join(tabs.keys()) or "нет"
        raise RecordSourceError(f"Таб '{tab_name}' не найден в {path}. Доступные: {available}")

    records = tabs[tab_name]
    if not isinstance(records, list):
        raise RecordSourceError(f"Таб '{tab_name}' в {path} должен быть массивом записей")

    return records


def _write_key_for(record: Dict[str, Any], position: int) -> WriteKey:
    """Ключ обратной записи: `_row_num`, если он есть, иначе позиция в массиве."""
    row_num = record.get(ROW_NUM_FIELD)
    if is_blank(row_num):
        return position
    return row_num


class JsonRecordSource:
    """Записи одного таба JSON-файла."""

    def __init__(self, path: Path, tab_name: str):
        """
        Args:
            path: Путь к JSON-файлу проекта
            tab_name: Название таба (аналог листа Excel)
        """
        self.path = Path(path)
        self.tab_name = tab_name
        self._raw: Optional[List[Dict[str, Any]]] = None
        self._parsed: Optional[List[Record]] = None

    def _raw_records(self) -> List[Dict[str, Any]]:
        if self._raw is None:
            self._raw = _get_tab(_load_tabs(self.path), self.path, self.tab_name)
        return self._raw

    def _where(self, position: int, write_key: Optional[WriteKey] = None) -> str:
        """Человекочитаемая привязка к месту в файле для сообщений об ошибках."""
        key = f"запись {write_key}" if write_key is not None else f"запись #{position}"
        return f"Таб '{self.tab_name}' ({self.path.name}), {key}"

    def _parse(self) -> List[Record]:
        """Разобрать и проверить ВЕСЬ таб сразу.

        Валидация целиком до синхронизации: иначе битая запись в середине
        таба всплыла бы уже после того, как предыдущие уехали в WordPress.
        """
        if self._parsed is not None:
            return self._parsed

        records: List[Record] = []
        seen_keys: Dict[WriteKey, int] = {}

        for position, record in enumerate(self._raw_records()):
            if not isinstance(record, dict):
                raise RecordSourceError(
                    f"{self._where(position)}: ожидался объект, "
                    f"получен {type(record).__name__}"
                )

            post_type = record.get(TYPE_FIELD)
            taxonomy = record.get(TAXONOMY_FIELD)
            has_type = not is_blank(post_type)
            has_taxonomy = not is_blank(taxonomy)
            write_key = _write_key_for(record, position)

            if write_key in seen_keys:
                raise RecordSourceError(
                    f"{self._where(position, write_key)}: ключ обратной записи "
                    f"{write_key!r} уже занят записью #{seen_keys[write_key]} — "
                    f"'{ROW_NUM_FIELD}' должен быть уникален внутри таба"
                )
            seen_keys[write_key] = position

            if has_type and has_taxonomy:
                raise RecordSourceError(
                    f"{self._where(position, write_key)}: заполнены одновременно "
                    f"'{TYPE_FIELD}' и '{TAXONOMY_FIELD}' — запись может быть "
                    "либо контентом, либо термином таксономии"
                )
            if not has_type and not has_taxonomy:
                raise RecordSourceError(
                    f"{self._where(position, write_key)}: не заполнено ни "
                    f"'{TYPE_FIELD}', ни '{TAXONOMY_FIELD}' — тип записи не определён "
                    "(служебные табы вроде 'options' этим источником не читаются)"
                )

            # `_row_num`/`type`/`taxonomy` — метаданные источника, а не поля записи:
            # они уже сидят в write_key/discriminator/is_taxonomy и не должны
            # уезжать в payload WordPress.
            data = {k: v for k, v in record.items() if k not in SOURCE_META_FIELDS}

            records.append(Record(
                discriminator=str(taxonomy if has_taxonomy else post_type).strip(),
                is_taxonomy=has_taxonomy,
                data=data,
                write_key=write_key,
            ))

        self._parsed = records
        return records

    def fields(self) -> List[str]:
        """Объединение полей всех записей таба (без метаданных источника)."""
        return collect_fields(self._parse())

    def read(self) -> Iterator[Record]:
        """Записи таба (весь таб проверяется до выдачи первой записи).

        Raises:
            RecordSourceError: запись не объект; `type`/`taxonomy` заполнены
                оба сразу или ни одного; повторяющийся ключ обратной записи.
        """
        return iter(self._parse())


class JsonRecordWriter:
    """Обратная запись результатов синхронизации в таб JSON-файла.

    Меняются только поля затронутых записей: остальные табы и записи
    остаются как были, файл переписывается только при реальных изменениях.

    В отличие от Excel, запись — это dict без фиксированной «шапки»:
    служебные поля (`NEW_FIELDS_ALWAYS`, прежде всего `id`) дописываются, даже
    если их не было в исходной записи; содержимое — только поверх своих полей.
    """

    def __init__(self, path: Path, tab_name: str, taxonomy_cache: Optional[Any] = None):
        """
        Args:
            path: Путь к JSON-файлу проекта
            tab_name: Название таба
            taxonomy_cache: TaxonomyCache для конвертации ID таксономий в имена
        """
        self.path = Path(path)
        self.tab_name = tab_name
        self._document: Optional[Dict[str, Any]] = None
        self._by_key: Dict[Any, Dict[str, Any]] = {}
        self._taxonomy_cache = taxonomy_cache
        self._lock = asyncio.Lock()
        # Dirty-флаг по аналогии с ExcelWriter: не переписываем файл вхолостую.
        self._dirty: bool = False

    def load(self) -> None:
        """Загрузить файл и проиндексировать записи таба по write_key.

        Raises:
            RecordSourceError: если две записи дают один и тот же write_key —
                иначе обратная запись молча ушла бы не в ту запись.
        """
        logger.debug(f"Загрузка JSON для записи: {self.path}")
        self._document = _load_tabs(self.path)
        records = _get_tab(self._document, self.path, self.tab_name)

        self._by_key = {}
        positions: Dict[Any, int] = {}
        for position, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            write_key = _write_key_for(record, position)
            if write_key in positions:
                raise RecordSourceError(
                    f"Таб '{self.tab_name}' ({self.path.name}): ключ обратной записи "
                    f"{write_key!r} встречается у записей #{positions[write_key]} и "
                    f"#{position} — '{ROW_NUM_FIELD}' должен быть уникален внутри таба"
                )
            positions[write_key] = position
            self._by_key[write_key] = record

    def update_record(self, write_key: WriteKey, data: Dict[str, Any]) -> None:
        """Обновить поля одной записи (отсутствующие поля добавляются)."""
        record = self._by_key.get(write_key)
        if record is None:
            logger.warning(f"Запись {write_key} не найдена в табе '{self.tab_name}'")
            return

        for field, value in data.items():
            record[field] = value
            self._dirty = True

        logger.debug(f"Обновлена запись {write_key} таба '{self.tab_name}': {list(data.keys())}")

    def _writeback_fields(self, record: Dict[str, Any]) -> List[str]:
        """Какие поля разрешено записать обратно.

        Поля самой записи плюс служебные (`NEW_FIELDS_ALWAYS`): у JSON нет
        фиксированной шапки, поэтому `id` созданного поста должен появиться даже
        в записи, где этого ключа не было. Содержимое (`content`, `title`, ACF и
        прочее из ответа API) добавляется только если поле уже есть в записи —
        иначе первый же sync залил бы в файл проекта весь ответ WordPress.
        """
        fields = {k for k in record if k not in SOURCE_META_FIELDS}
        fields.update(NEW_FIELDS_ALWAYS)
        return list(fields)

    def update_from_result(
        self,
        write_key: WriteKey,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None,
    ) -> None:
        """Обновить запись данными из ответа WordPress API."""
        record = self._by_key.get(write_key)
        if record is None:
            logger.warning(f"Запись {write_key} не найдена в табе '{self.tab_name}'")
            return

        update_data = build_update_data_from_api(
            api_response,
            action,
            self._writeback_fields(record),
            taxonomy_cache=self._taxonomy_cache,
            columns_filter=columns_filter,
        )
        if update_data:
            self.update_record(write_key, update_data)

    async def update_from_result_async(
        self,
        write_key: WriteKey,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None,
    ) -> None:
        """Async-версия update_from_result (запись под общим Lock)."""
        async with self._lock:
            self.update_from_result(write_key, api_response, action, columns_filter=columns_filter)

    def save(self) -> None:
        """Сохранить файл, если что-то менялось.

        Пишем во временный файл рядом и подменяем через `os.replace()`:
        падение посреди записи не должно обрезать данные проекта.
        """
        if not self._dirty:
            logger.info("JSON-файл не изменён — save() пропущен")
            return

        if self._document is None:
            return

        logger.info(f"Сохранение изменений в JSON: {self.path}")
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        tmp_path.write_text(
            json.dumps(self._document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)
        self._dirty = False

    def __enter__(self) -> "JsonRecordWriter":
        self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.save()
