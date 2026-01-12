"""
Чтение Excel файлов с множественными листами
"""

from pathlib import Path
from typing import Dict, List, Literal
import pandas as pd

from ..constants import SYSTEM_SHEETS, TAXONOMY_SUFFIX
from ..exceptions import ExcelError, ExcelStructureError


SheetType = Literal["profile", "options", "content", "taxonomy", "media"]


class ExcelReader:
    """Читает и классифицирует листы Excel файла"""

    def __init__(self, excel_file: Path):
        self.excel_file = excel_file
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

        # Стандартные таксономии (category, categories, tag, tags) или с суффиксом -cat
        if sheet_name in ["category", "categories", "tag", "tags"] or sheet_name.endswith(TAXONOMY_SUFFIX):
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

            # Заполнение NaN
            if fillna:
                df = df.fillna("")

            # Кэшируем
            self._sheets_cache[sheet_name] = df.copy()

            return df

        except ValueError as e:
            if "Worksheet named" in str(e) and "not found" in str(e):
                raise ExcelError(f"Лист '{sheet_name}' не найден в файле {self.excel_file}")
            raise

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
