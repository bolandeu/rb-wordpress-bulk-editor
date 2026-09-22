"""
JSON-вывод: ровно тот конверт, что описан в conventions.md.

Успех:  {"ok": true, ...поля команды}
Ошибка: {"ok": false, "error": {"code", "message", "action"?}}

Конверт ошибки тоже идёт в stdout: агенту так нужен один поток на разбор,
а то, что это ошибка, видно по `ok: false` и exit-коду.
"""

import json as _json
import sys
from typing import Any, Dict

from ..exceptions import WPBulkEditorError


def _dump(data: Dict[str, Any]) -> None:
    sys.stdout.write(_json.dumps(data, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def emit_success(data: Dict[str, Any]) -> None:
    """Конверт успеха: {"ok": true, ...data}."""
    _dump({"ok": True, **data})


def emit_error(error: WPBulkEditorError) -> None:
    """Конверт ошибки: {"ok": false, "error": {...}}."""
    _dump({"ok": False, "error": error.to_dict()})
