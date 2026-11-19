"""
Хранилище табов: команды читают и пишут «набор табов», не зная носителя.

Таб — имя и список записей-словарей (лист Excel, ключ `tabs` в JSON). Носитель
выбирается один раз, в `open_store()`, по расширению `--file`; новый носитель
(база данных и т.п.) — это новый адаптер в `_ADAPTERS`, а не правка команд.

Адаптер отвечает только за перевод модели «табы → записи» в свой формат:
`_row_num` — забота JSON (ключ обратной записи), в Excel его роль играет
номер строки, поэтому колонкой он туда не пишется.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from ..exceptions import ValidationError
from . import json_source

TabRecords = List[Dict[str, Any]]


class TabStore(Protocol):
    """Носитель набора табов."""

    path: Path

    @property
    def format(self) -> str:
        """Имя формата для конверта (`json`, `xlsx`)."""
        ...

    def read_tab(self, name: str) -> Optional[TabRecords]:
        """Записи таба; None — нет носителя или такого таба."""
        ...

    def write_tab(self, name: str, records: TabRecords) -> None:
        """Заменить таб целиком; остальные табы не трогаются."""
        ...


class JsonTabStore:
    """JSON-файл данных `{"project", "tabs": {таб: [записи]}}` — транспортный формат."""

    format = "json"

    def __init__(self, path: Path, project: Optional[str] = None):
        self.path = Path(path)
        self.project = project

    def read_tab(self, name: str) -> Optional[TabRecords]:
        return json_source.read_tab_if_exists(self.path, name)

    def write_tab(self, name: str, records: TabRecords) -> None:
        # Таб пересобирается целиком — `_row_num` сквозной, с номера первой
        # строки данных под шапкой (как у fetch), и идёт первым полем записи.
        numbered = [
            {json_source.ROW_NUM_FIELD: 2 + i, **{k: v for k, v in record.items() if k != json_source.ROW_NUM_FIELD}}
            for i, record in enumerate(records)
        ]
        json_source.write_tab(self.path, name, numbered, project=self.project)


class ExcelTabStore:
    """Книга xlsx: таб = лист, первая строка — шапка. Интерфейс человека."""

    format = "xlsx"

    def __init__(self, path: Path, project: Optional[str] = None):
        self.path = Path(path)

    def read_tab(self, name: str) -> Optional[TabRecords]:
        if not self.path.exists():
            return None
        from openpyxl import load_workbook

        workbook = load_workbook(self.path, read_only=True, data_only=True)
        try:
            if name not in workbook.sheetnames:
                return None
            rows = workbook[name].iter_rows(values_only=True)
            header = next(rows, None)
            if header is None:
                return []
            columns = [str(cell).strip() if cell is not None else "" for cell in header]
            records = []
            for row in rows:
                record = {col: value for col, value in zip(columns, row) if col}
                if any(value not in (None, "") for value in record.values()):
                    records.append(record)
            return records
        finally:
            workbook.close()

    def write_tab(self, name: str, records: TabRecords) -> None:
        from openpyxl import Workbook, load_workbook

        if self.path.exists():
            workbook = load_workbook(self.path)
            position = workbook.sheetnames.index(name) if name in workbook.sheetnames else len(workbook.sheetnames)
            if name in workbook.sheetnames:
                del workbook[name]
            sheet = workbook.create_sheet(name, position)
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = name

        columns: List[str] = []
        for record in records:
            for key in record:
                if key != json_source.ROW_NUM_FIELD and key not in columns:
                    columns.append(key)
        sheet.append(columns)
        for record in records:
            sheet.append([_cell(record.get(col)) for col in columns])

        self.path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(self.path)


def _cell(value: Any) -> Any:
    """Значение для ячейки: списки и словари openpyxl не пишет — сериализуем."""
    if isinstance(value, (list, dict)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return value


_ADAPTERS: Dict[str, Callable[..., TabStore]] = {
    ".json": JsonTabStore,
    ".xlsx": ExcelTabStore,
}


def open_store(path: Path, project: Optional[str] = None) -> TabStore:
    """
    Носитель по `--file`: единственное место, где выбирается формат.

    Raises:
        ValidationError: формат не поддерживается (exit 2).
    """
    path = Path(path)
    adapter = _ADAPTERS.get(path.suffix.lower())
    if adapter is None:
        supported = ", ".join(sorted(_ADAPTERS))
        raise ValidationError(f"Неизвестный формат файла {path.name}: поддерживаются {supported}")
    return adapter(path, project=project)
