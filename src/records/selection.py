"""
Выбор источника и наборов данных (лист Excel / таб JSON) по аргументам CLI.

Общее для всех команд, умеющих оба формата (`sync`, `async`, `update`,
`validate`): источник определяется расширением файла, а для JSON набор данных
выбирается только явно — рядом с контентом в файле лежат служебные табы.
"""

from pathlib import Path
from typing import List, Optional

from ..exceptions import ValidationError
from .json_source import tab_names


def is_json_source(data_file: Path) -> bool:
    """Источник выбирается по расширению файла данных: .json → JSON, иначе Excel."""
    return Path(data_file).suffix.lower() == ".json"


def dataset_names(sheets_filter: str) -> List[str]:
    """
    Разобрать `--sheets` в список имён наборов данных.

    Пустой результат (`--sheets ","`, `--sheets ""`) — ошибка аргументов:
    иначе прогон молча не обработал бы ничего и отчитался успехом.

    Raises:
        ValidationError: ни одного имени после разбора (exit 2).
    """
    names = [name.strip() for name in (sheets_filter or "").split(",") if name.strip()]
    if not names:
        raise ValidationError(
            f"--sheets не содержит ни одного имени набора данных: {sheets_filter!r}. "
            "Укажите листы Excel / табы JSON через запятую."
        )
    return names


def require_json_tabs(path: Path, sheets_filter: Optional[str]) -> List[str]:
    """
    Проверить выбор табов для JSON-источника.

    Вызывается до загрузки credentials: «какие табы есть в файле» — вопрос
    к файлу, агент должен получить этот список без доступа к сайту.

    Raises:
        ValidationError: таб не выбран или такого таба в файле нет (exit 2).
    """
    path = Path(path)
    available = tab_names(path)
    listed = ", ".join(available) or "нет"

    if not sheets_filter:
        raise ValidationError(
            f"Для JSON-файла {path.name} нужно указать --sheets: значения по умолчанию "
            "нет, потому что часть табов служебные (options, company), а не контент. "
            f"Доступные табы: {listed}"
        )

    requested = dataset_names(sheets_filter)
    unknown = [tab for tab in requested if tab not in available]
    if unknown:
        raise ValidationError(
            f"Таб(ы) не найдены в {path.name}: {', '.join(unknown)}. Доступные: {listed}"
        )

    return requested


def validate_dataset_selection(data_file: Path, sheets_filter: Optional[str]) -> None:
    """Проверить выбор наборов данных до загрузки credentials (оба формата)."""
    if is_json_source(data_file):
        require_json_tabs(data_file, sheets_filter)
    elif sheets_filter is not None:
        dataset_names(sheets_filter)
