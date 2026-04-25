"""
Чтение Excel файлов с множественными листами
"""

from pathlib import Path
from typing import Dict, List, Literal, Optional
import pandas as pd
from loguru import logger

from ..constants import SYSTEM_SHEETS, VALID_ACTIONS, is_taxonomy_column
from ..exceptions import ExcelError, ExcelStructureError


SheetType = Literal["profile", "options", "content", "taxonomy", "media"]


class ExcelReader:
    """Читает и классифицирует листы Excel файла"""

    def __init__(self, excel_file: Path, known_taxonomies: Optional[List[str]] = None):
        """
        Args:
            excel_file: Путь к xlsx.
            known_taxonomies: Явный список таксономий проекта (из .env/discovery).
                Листы с такими именами классифицируются как taxonomy даже без `-cat` суффикса.
        """
        self.excel_file = excel_file
        self.known_taxonomies = known_taxonomies
        self._excel_file_obj = None
        self._sheets_cache: Dict[str, pd.DataFrame] = {}

    def __enter__(self):
        """Контекстный менеджер для работы с Excel файлом"""
        self._excel_file_obj = pd.ExcelFile(self.excel_file)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Закрытие Excel файла"""
        if self._excel_file_obj is not None:
            self._excel_file_obj.close()

    def get_sheet_names(self) -> List[str]:
        """Получить список всех листов"""
        if self._excel_file_obj is None:
            with pd.ExcelFile(self.excel_file) as xls:
                return xls.sheet_names
        return self._excel_file_obj.sheet_names

    def classify_sheet(self, sheet_name: str) -> SheetType:
        """
        Классифицировать тип листа.

        Args:
            sheet_name: Название листа

        Returns:
            Тип листа: profile, options, content, taxonomy, media
        """
        if sheet_name == "profile":
            return "profile"

        if sheet_name == "options":
            return "options"

        if sheet_name == "media":
            return "media"

        # Таксономии: явный список проекта, иначе стандартные + `-cat` суффикс
        if is_taxonomy_column(sheet_name, self.known_taxonomies):
            return "taxonomy"

        # Все остальное - контент
        return "content"

    def get_sheets_by_type(self) -> Dict[SheetType, List[str]]:
        """
        Группировка листов по типам.

        Returns:
            Dict с ключами типов и списками названий листов
        """
        sheets = self.get_sheet_names()
        result: Dict[SheetType, List[str]] = {
            "profile": [],
            "options": [],
            "content": [],
            "taxonomy": [],
            "media": []
        }

        for sheet_name in sheets:
            sheet_type = self.classify_sheet(sheet_name)
            result[sheet_type].append(sheet_name)

        return result

    def read_sheet(
        self,
        sheet_name: str,
        dtype: Dict[str, type] = None,
        fillna: bool = True
    ) -> pd.DataFrame:
        """
        Читает конкретный лист Excel.

        Args:
            sheet_name: Название листа
            dtype: Типы данных для колонок
            fillna: Заполнять ли NaN пустыми строками

        Returns:
            DataFrame с данными листа

        Raises:
            ExcelError: Если лист не найден
        """
        # Проверяем кэш
        if sheet_name in self._sheets_cache:
            return self._sheets_cache[sheet_name].copy()

        try:
            if self._excel_file_obj is not None:
                df = pd.read_excel(
                    self._excel_file_obj,
                    sheet_name=sheet_name,
                    dtype=dtype or str  # По умолчанию все как строки
                )
            else:
                df = pd.read_excel(
                    self.excel_file,
                    sheet_name=sheet_name,
                    dtype=dtype or str
                )

            # Игнорируем колонки без заголовка (pandas: "Unnamed: N") и служебные `__*`
            df = self._drop_ignored_columns(df, sheet_name)

            # Заполнение NaN
            if fillna:
                df = df.fillna("")

            # Игнорируем пустые строки и строки без минимума (action / title / name)
            df = self._drop_ignored_rows(df, sheet_name)

            # Кэшируем
            self._sheets_cache[sheet_name] = df.copy()

            return df

        except ValueError as e:
            if "Worksheet named" in str(e) and "not found" in str(e):
                raise ExcelError(f"Лист '{sheet_name}' не найден в файле {self.excel_file}")
            raise

    @staticmethod
    def _is_ignored_column(name: object) -> bool:
        """Колонка игнорируется: пустой заголовок (pandas → `Unnamed: N`) или префикс `__`."""
        if name is None:
            return True
        if not isinstance(name, str):
            name = str(name)
        stripped = name.strip()
        if not stripped:
            return True
        if stripped.startswith("__"):
            return True
        if stripped.startswith("Unnamed:"):
            return True
        return False

    def _drop_ignored_columns(self, df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
        """Удаляет колонки с пустым заголовком и `__*`. Логирует только если что-то отфильтровано."""
        ignored = [c for c in df.columns if self._is_ignored_column(c)]
        if not ignored:
            return df
        logger.debug(
            f"Лист '{sheet_name}': игнорируются колонки без заголовка/служебные: {ignored}"
        )
        return df.drop(columns=ignored)

    @staticmethod
    def _is_blank_value(v: object) -> bool:
        """Пустая ячейка: None, NaN или строка из одних пробелов."""
        if v is None:
            return True
        try:
            if pd.isna(v):
                return True
        except (TypeError, ValueError):
            pass
        if isinstance(v, str):
            return v.strip() == ""
        return False

    # Минимально достаточный набор полей: хотя бы одно из них должно быть заполнено.
    # `action` нужен для sync; `title`/`name` — для контента/таксономий соответственно.
    _MINIMAL_ROW_COLUMNS = ("action", "title", "name")

    def _drop_ignored_rows(self, df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
        """Удаляет полностью пустые строки и строки без минимума (action/title/name).

        Индекс не сбрасываем: writer полагается на исходные позиции строк
        (см. `ContentSync.iterrows()` → `ExcelWriter.update_row`).
        """
        if df.empty:
            return df

        blank = df.map(self._is_blank_value) if hasattr(df, "map") else df.applymap(self._is_blank_value)

        fully_empty = blank.all(axis=1)

        minimal_cols = [c for c in self._MINIMAL_ROW_COLUMNS if c in df.columns]
        if minimal_cols:
            no_minimal = blank[minimal_cols].all(axis=1)
        else:
            no_minimal = pd.Series(False, index=df.index)

        drop_mask = fully_empty | no_minimal
        if not drop_mask.any():
            return df

        logger.debug(
            f"Лист '{sheet_name}': пропущено {int(drop_mask.sum())} строк "
            f"(пустые / без {'/'.join(minimal_cols) or 'данных'})"
        )
        return df[~drop_mask]

    def validate_sheet_structure(
        self,
        sheet_name: str,
        required_columns: List[str]
    ) -> bool:
        """
        Валидация структуры листа.

        Args:
            sheet_name: Название листа
            required_columns: Список обязательных колонок

        Returns:
            True если структура валидна

        Raises:
            ExcelStructureError: Если структура неверная
        """
        df = self.read_sheet(sheet_name)

        missing_columns = set(required_columns) - set(df.columns)
        if missing_columns:
            raise ExcelStructureError(
                f"В листе '{sheet_name}' отсутствуют обязательные колонки: "
                f"{', '.join(missing_columns)}"
            )

        return True

    def filter_by_action(
        self,
        df: pd.DataFrame,
        action: str
    ) -> pd.DataFrame:
        """
        Фильтрует строки по значению action.

        Args:
            df: DataFrame
            action: Значение action (add, update, delete)

        Returns:
            Отфильтрованный DataFrame
        """
        if "action" not in df.columns:
            return pd.DataFrame()

        # Приводим к нижнему регистру и сравниваем
        mask = df["action"].str.lower().str.strip() == action.lower()
        return df[mask].copy()

    def get_content_columns(self, sheet_name: str) -> List[str]:
        """
        Получить список колонок контента (без служебных).

        Args:
            sheet_name: Название листа

        Returns:
            Список названий колонок
        """
        df = self.read_sheet(sheet_name)
        return df.columns.tolist()

    def check_duplicate_ids(self, df: pd.DataFrame, sheet_name: str) -> List[str]:
        """
        Проверить наличие дублирующихся ID в листе.

        Args:
            df: DataFrame листа
            sheet_name: Название листа (для логов)

        Returns:
            Список дублирующихся ID
        """
        if "id" not in df.columns:
            return []

        ids = df["id"].dropna()
        ids = ids[ids != ""]
        duplicates = ids[ids.duplicated()].tolist()

        if duplicates:
            logger.warning(
                f"Лист '{sheet_name}': найдены дублирующиеся ID: {duplicates}. "
                "Это может привести к непредвиденному поведению."
            )

        return duplicates

    def validate_actions(self, df: pd.DataFrame, sheet_name: str) -> List[str]:
        """
        Проверить корректность значений колонки action.

        Args:
            df: DataFrame листа
            sheet_name: Название листа (для логов)

        Returns:
            Список строк с недопустимыми значениями action
        """
        if "action" not in df.columns:
            return []

        allowed = VALID_ACTIONS | {"", "skip"}
        actions = df["action"].str.lower().str.strip()
        invalid_mask = ~actions.isin(allowed)
        invalid_values = actions[invalid_mask].tolist()

        if invalid_values:
            logger.warning(
                f"Лист '{sheet_name}': недопустимые значения action: {list(set(invalid_values))}. "
                f"Допустимые: {VALID_ACTIONS}. Строки будут пропущены."
            )

        return invalid_values
