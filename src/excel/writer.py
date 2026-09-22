"""
Запись данных обратно в Excel файл
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional
import pandas as pd
from openpyxl import load_workbook
from loguru import logger

from ..exceptions import ExcelError
from ..mappers.writeback_mapper import build_update_data_from_api, normalize_api_value


EXCEL_CELL_CHAR_LIMIT = 32767


def _coerce_for_column(column: pd.Series, value: Any) -> Any:
    """Привести значение к типу колонки DataFrame перед записью.

    pandas 2.x по умолчанию выводит `StringDtype` для колонок-строк и падает
    при попытке записать int/float. Кастим к строке только для строковых колонок;
    NaN/None сохраняются как NaN.
    """
    if pd.api.types.is_string_dtype(column):
        if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
            return pd.NA
        return str(value)
    return value


def _find_oversized_cells(
    dataframes: Dict[str, pd.DataFrame],
    limit: int = EXCEL_CELL_CHAR_LIMIT,
) -> List[str]:
    """Найти значения, которые Excel всё равно обрежет при сохранении."""
    problems = []

    for sheet_name, df in dataframes.items():
        for column in df.columns:
            series = df[column].dropna()
            for index, value in series.items():
                if isinstance(value, str) and len(value) > limit:
                    excel_row = int(index) + 2  # +1 header, +1 zero-based index
                    problems.append(
                        f"{sheet_name}!{column}{excel_row}: {len(value)} chars"
                    )

    return problems


class ExcelWriter:
    """
    Записывает данные обратно в Excel файл после синхронизации.
    Обновляет ID, даты, ссылки и другие поля.

    Thread-safe для async операций через asyncio.Lock.
    """

    def __init__(
        self,
        excel_file: Path,
        taxonomy_cache: Optional[Any] = None
    ):
        """
        Args:
            excel_file: Путь к Excel файлу
            taxonomy_cache: Объект TaxonomyCache для динамической конвертации ID таксономий
        """
        self.excel_file = excel_file
        self._workbook = None
        self._dataframes: Dict[str, pd.DataFrame] = {}
        self._locks: Dict[str, asyncio.Lock] = {}  # NEW: Lock per sheet
        self._taxonomy_cache = taxonomy_cache
        # Dirty-флаг: save() переписывает весь файл через pd.ExcelWriter и
        # теряет формат/комментарии/мердж-ячейки. Не пишем, если ничего не меняли.
        self._dirty: bool = False

    def load(self):
        """Загрузить Excel файл для редактирования."""
        logger.debug(f"Loading Excel file for writing: {self.excel_file}")
        self._workbook = load_workbook(self.excel_file)

        # Загружаем все листы в DataFrame
        with pd.ExcelFile(self.excel_file) as xls:
            for sheet_name in xls.sheet_names:
                self._dataframes[sheet_name] = pd.read_excel(
                    xls,
                    sheet_name=sheet_name,
                    dtype=str
                )
                # NEW: Создаем lock для каждого листа
                self._locks[sheet_name] = asyncio.Lock()

    def update_row(
        self,
        sheet_name: str,
        row_index: int,
        data: Dict[str, Any]
    ):
        """
        Обновить строку в листе.

        Args:
            sheet_name: Название листа
            row_index: Индекс строки (0-based, как в pandas)
            data: Словарь {column_name: value} для обновления
        """
        if sheet_name not in self._dataframes:
            logger.warning(f"Sheet {sheet_name} not found")
            return

        df = self._dataframes[sheet_name]

        # Проверяем что индекс валиден
        if row_index < 0 or row_index >= len(df):
            logger.warning(f"Row index {row_index} out of range for sheet {sheet_name}")
            return

        # Обновляем значения
        for column, value in data.items():
            if column in df.columns:
                df.at[row_index, column] = _coerce_for_column(df[column], value)
                self._dirty = True
            else:
                logger.warning(f"Column {column} not found in sheet {sheet_name}")

        logger.debug(f"Updated row {row_index} in sheet {sheet_name}: {list(data.keys())}")

    def append_rows(
        self,
        sheet_name: str,
        rows: list[Dict[str, Any]]
    ):
        """
        Добавить новые строки в конец листа.

        Args:
            sheet_name: Название листа
            rows: Список словарей с данными для новых строк
        """
        if sheet_name not in self._dataframes:
            logger.warning(f"Sheet {sheet_name} not found")
            return

        if not rows:
            return

        df = self._dataframes[sheet_name]

        # Создаем DataFrame из новых строк
        new_df = pd.DataFrame(rows)

        # Убеждаемся что все старые колонки существуют
        for col in df.columns:
            if col not in new_df.columns:
                new_df[col] = ""

        # Сохраняем старый порядок и добавляем новые колонки из fetch/мапперов.
        # Это важно для content_path: старые Excel-файлы могли быть без этой колонки.
        ordered_columns = list(df.columns) + [
            col for col in new_df.columns if col not in df.columns
        ]
        new_df = new_df[ordered_columns]

        # Добавляем новые строки
        self._dataframes[sheet_name] = pd.concat(
            [df, new_df],
            ignore_index=True
        )
        self._dirty = True

        logger.info(f"Appended {len(rows)} rows to sheet {sheet_name}")

    def replace_rows(
        self,
        sheet_name: str,
        rows: list[Dict[str, Any]]
    ):
        """
        Заменить все строки в листе новыми данными (кроме заголовков).

        Args:
            sheet_name: Название листа
            rows: Список словарей с данными для новых строк
        """
        if sheet_name not in self._dataframes:
            logger.warning(f"Sheet {sheet_name} not found")
            return

        if not rows:
            logger.warning(f"No rows to replace in sheet {sheet_name}")
            return

        df = self._dataframes[sheet_name]

        # Создаем новый DataFrame из новых строк
        new_df = pd.DataFrame(rows)

        # Убеждаемся что все старые колонки существуют
        for col in df.columns:
            if col not in new_df.columns:
                new_df[col] = ""

        # Сохраняем старый порядок и добавляем новые колонки из fetch/мапперов.
        # Это важно для content_path: старые Excel-файлы могли быть без этой колонки.
        ordered_columns = list(df.columns) + [
            col for col in new_df.columns if col not in df.columns
        ]
        new_df = new_df[ordered_columns]

        # Заменяем все строки новыми данными
        self._dataframes[sheet_name] = new_df
        self._dirty = True

        logger.info(f"Replaced all rows in sheet {sheet_name} with {len(rows)} new rows")

    def _build_update_data_from_api(
        self,
        sheet_name: str,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Построить словарь данных для обновления строки из ответа WordPress API.

        Единая логика для sync и async методов (см. mappers/writeback_mapper.py).

        Args:
            sheet_name: Название листа
            api_response: Ответ от WordPress API
            action: Тип операции (add, update, delete)
            columns_filter: Если задан — минимальный writeback (action, modified, id)

        Returns:
            Словарь {column_name: value} для обновления
        """
        # Получаем существующие колонки
        if sheet_name not in self._dataframes:
            return {}

        return build_update_data_from_api(
            api_response,
            action,
            self._dataframes[sheet_name].columns.tolist(),
            taxonomy_cache=self._taxonomy_cache,
            columns_filter=columns_filter,
        )

    def update_from_api_response(
        self,
        sheet_name: str,
        row_index: int,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None
    ):
        """
        Обновить строку данными из ответа WordPress API.

        Args:
            sheet_name: Название листа
            row_index: Индекс строки
            api_response: Ответ от WordPress API
            action: Тип операции (add, update, delete)
            columns_filter: Если задан — ограничить writeback (только action, modified, id)
        """
        update_data = self._build_update_data_from_api(
            sheet_name, api_response, action, columns_filter=columns_filter
        )
        if update_data:
            self.update_row(sheet_name, row_index, update_data)

    # ========================================
    # Async methods with Lock (NEW)
    # ========================================

    async def update_row_async(
        self,
        sheet_name: str,
        row_index: int,
        data: Dict[str, Any]
    ):
        """
        Async версия update_row с thread-safety через Lock.

        Args:
            sheet_name: Название листа
            row_index: Индекс строки (0-based, как в pandas)
            data: Словарь {column_name: value} для обновления
        """
        # Создаем lock если его нет (для случаев когда лист добавлен после load())
        if sheet_name not in self._locks:
            self._locks[sheet_name] = asyncio.Lock()

        # Блокируем доступ на время обновления
        async with self._locks[sheet_name]:
            # Вызываем существующий sync метод
            self.update_row(sheet_name, row_index, data)

    async def update_from_api_response_async(
        self,
        sheet_name: str,
        row_index: int,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None
    ):
        """
        Async версия update_from_api_response с thread-safety.

        Args:
            sheet_name: Название листа
            row_index: Индекс строки
            api_response: Ответ от WordPress API
            action: Тип операции (add, update, delete)
            columns_filter: Если задан — ограничить writeback (только action, modified, id)
        """
        # Формируем update_data (вне блокировки для минимизации времени lock)
        update_data = self._build_update_data_from_api(
            sheet_name, api_response, action, columns_filter=columns_filter
        )

        # Блокируем только момент записи
        if update_data:
            await self.update_row_async(sheet_name, row_index, update_data)

    def save(self):
        """
        Сохранить изменения в Excel файл.

        No-op, если ни один update_row/append/replace не был вызван — иначе
        pd.ExcelWriter переписал бы файл «в холостую», теряя форматирование
        исходного xlsx (read-only команды не должны трогать файл).
        """
        if not self._dirty:
            logger.info("Excel файл не изменён — save() пропущен")
            return

        logger.info(f"Saving changes to Excel file: {self.excel_file}")

        try:
            oversized_cells = _find_oversized_cells(self._dataframes)
            if oversized_cells:
                details = "; ".join(oversized_cells[:20])
                extra = "" if len(oversized_cells) <= 20 else f"; ... and {len(oversized_cells) - 20} more"
                raise ExcelError(
                    "Excel cell limit exceeded. Values longer than "
                    f"{EXCEL_CELL_CHAR_LIMIT} characters must be moved to files "
                    f"before saving: {details}{extra}"
                )

            # Используем ExcelWriter из pandas для записи всех листов
            with pd.ExcelWriter(
                self.excel_file,
                engine="openpyxl",
                mode="w"
            ) as writer:
                for sheet_name, df in self._dataframes.items():
                    df.to_excel(
                        writer,
                        sheet_name=sheet_name,
                        index=False
                    )

            logger.info(f"Successfully saved Excel file")
            self._dirty = False

        except Exception as e:
            logger.error(f"Failed to save Excel file: {e}")
            raise

    def __enter__(self):
        """Контекстный менеджер."""
        self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Автоматическое сохранение при выходе."""
        if exc_type is None:
            # Сохраняем только если не было ошибок
            self.save()
