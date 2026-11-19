"""
Счётчик прогресса синхронизации одного набора данных (лист Excel / таб JSON).

По завершении каждой записи в лог уходит строка `[17/104] id=3425 updated`:
по ней вызывающий (antagor читает поток лога) видит, на какой записи прогон и
сколько осталось. Снаружи это не вычислить — номер записи знает только тот,
кто её обработал.

В async записи завершаются не по порядку: номер — это сколько записей уже
сделано, а не позиция записи в файле. Счётчик меняется синхронно между
await'ами одного event loop, поэтому блокировка не нужна.
"""

from typing import Any, Mapping, Optional

from loguru import logger

from ..records.base import Record, is_blank

# action записи → итог в строке прогресса
_OUTCOMES = {"add": "added", "update": "updated", "delete": "deleted"}


class Progress:
    """Сколько записей набора уже обработано из общего числа."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0

    def skipped(self, count: int) -> None:
        """Записи, которые sync не трогает (skip / пустой action), — одной строкой."""
        if count <= 0:
            return
        self.done += count
        logger.info(f"[{self.done}/{self.total}] пропущено записей (skip/пустой action): {count}")

    def record_done(
        self,
        record: Record,
        action: str,
        response: Optional[Mapping[str, Any]] = None,
        error: bool = False,
    ) -> None:
        """Запись обработана: успешно (с ответом API) или с ошибкой."""
        self.done += 1
        outcome = "error" if error else _OUTCOMES.get(action, action)
        record_id = _record_id(record, response)
        where = f"id={record_id}" if record_id is not None else "id=-"
        suffix = f" (запись {record.write_key})" if record_id is None else ""
        logger.info(f"[{self.done}/{self.total}] {where} {outcome}{suffix}")


def _record_id(record: Record, response: Optional[Mapping[str, Any]]) -> Optional[Any]:
    """id записи на сайте: из ответа API, иначе из самой записи.

    Ответ dry-run несёт заглушку `id=0` — это не id, берём из записи.
    """
    if response and response.get("id"):
        return response["id"]
    value = record.data.get("id")
    if is_blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
