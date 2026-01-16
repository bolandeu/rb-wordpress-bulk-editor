"""
Запись данных обратно в Excel файл
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd
from openpyxl import load_workbook
from loguru import logger


def normalize_api_value(value: Any) -> Any:
    """
    Нормализовать значение из WordPress API для записи в Excel.

    WordPress API возвращает некоторые поля как объекты:
    - title: {'raw': 'Title', 'rendered': 'Title'}
    - content: {'raw': '...', 'rendered': '...', 'protected': False}
    - excerpt: {'raw': '...', 'rendered': '...'}
    - name (taxonomies): иногда тоже объект

    Args:
        value: Значение из API

    Returns:
        Нормализованное значение (строка или исходное значение)
    """
    if isinstance(value, dict):
        # Если есть 'raw' - берём его (это исходное значение без HTML обёртки)
        if 'raw' in value:
            return value['raw']
        # Если есть 'rendered' но нет 'raw' - берём rendered
        if 'rendered' in value:
            return value['rendered']
    return value


class ExcelWriter:
    """
    Записывает данные обратно в Excel файл после синхронизации.
    Обновляет ID, даты, ссылки и другие поля.

    Thread-safe для async операций через asyncio.Lock.
    """

    def __init__(self, excel_file: Path):
        self.excel_file = excel_file
        self._workbook = None
        self._dataframes: Dict[str, pd.DataFrame] = {}
        self._locks: Dict[str, asyncio.Lock] = {}  # NEW: Lock per sheet

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
                df.at[row_index, column] = value
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

        # Убеждаемся что все колонки существуют
        for col in df.columns:
            if col not in new_df.columns:
                new_df[col] = ""

        # Сохраняем порядок колонок как в исходном листе
        new_df = new_df[df.columns]

        # Добавляем новые строки
        self._dataframes[sheet_name] = pd.concat(
            [df, new_df],
            ignore_index=True
        )

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

        # Убеждаемся что все колонки существуют
        for col in df.columns:
            if col not in new_df.columns:
                new_df[col] = ""

        # Сохраняем порядок колонок как в исходном листе
        new_df = new_df[df.columns]

        # Заменяем все строки новыми данными
        self._dataframes[sheet_name] = new_df

        logger.info(f"Replaced all rows in sheet {sheet_name} with {len(rows)} new rows")

    def _build_update_data_from_api(
        self,
        sheet_name: str,
        api_response: Dict[str, Any],
        action: str
    ) -> Dict[str, Any]:
        """
        Построить словарь данных для обновления строки из ответа WordPress API.

        Единая логика для sync и async методов.

        Args:
            sheet_name: Название листа
            api_response: Ответ от WordPress API
            action: Тип операции (add, update, delete)

        Returns:
            Словарь {column_name: value} для обновления
        """
        update_data = {}

        # Получаем существующие колонки
        if sheet_name not in self._dataframes:
            return update_data

        df = self._dataframes[sheet_name]
        existing_columns = df.columns.tolist()

        # ID (для add операций)
        if action == "add" and "id" in api_response and "id" in existing_columns:
            update_data["id"] = api_response["id"]

        # Даты
        if "date" in api_response and "date" in existing_columns:
            update_data["date"] = api_response["date"]

        if "modified" in api_response and "modified" in existing_columns:
            update_data["modified"] = api_response["modified"]

        # Ссылка
        if "link" in api_response and "link" in existing_columns:
            update_data["link"] = api_response["link"]

        # Slug
        if "slug" in api_response and "slug" in existing_columns:
            update_data["slug"] = api_response["slug"]

        # Featured media
        if "featured_media" in api_response and api_response["featured_media"] and "featured_media" in existing_columns:
            update_data["featured_media"] = api_response["featured_media"]

        # Image URL (кастомное поле из API)
        if "image_url" in api_response and "image_url" in existing_columns:
            update_data["image_url"] = api_response["image_url"]

        # Поля с rendered/raw структурой - извлекаем raw значение
        # title, content, excerpt (posts), name, description (taxonomies)
        for field in ("title", "content", "excerpt", "name", "description"):
            if field in api_response and field in existing_columns:
                update_data[field] = normalize_api_value(api_response[field])

        # ACF поля из ответа API (обновляем только те, что есть в Excel)
        acf = api_response.get("acf", {})
        if acf and isinstance(acf, dict):
            for key, value in acf.items():
                acf_col = f"acf_{key}"
                if acf_col in existing_columns:
                    update_data[acf_col] = normalize_api_value(value)

        # Другие кастомные поля (обновляем только те, что есть в Excel)
        skip_fields = {
            "id", "date", "modified", "link", "slug", "featured_media", "image_url", "acf",
            "title", "content", "excerpt", "name", "description"
        }
        for key, value in api_response.items():
            if key not in skip_fields and key not in update_data and key in existing_columns:
                update_data[key] = normalize_api_value(value)

        # После успешной операции меняем action на skip
        if "action" in existing_columns:
            update_data["action"] = "skip"

        # Очищаем content_path и img_path если они были заполнены
        if "content_path" in existing_columns:
            update_data["content_path"] = ""
        if "img_path" in existing_columns:
            update_data["img_path"] = ""

        return update_data

    def update_from_api_response(
        self,
        sheet_name: str,
        row_index: int,
        api_response: Dict[str, Any],
        action: str
    ):
        """
        Обновить строку данными из ответа WordPress API.

        Args:
            sheet_name: Название листа
            row_index: Индекс строки
            api_response: Ответ от WordPress API
            action: Тип операции (add, update, delete)
        """
        update_data = self._build_update_data_from_api(sheet_name, api_response, action)
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
        action: str
    ):
        """
        Async версия update_from_api_response с thread-safety.

        Args:
            sheet_name: Название листа
            row_index: Индекс строки
            api_response: Ответ от WordPress API
            action: Тип операции (add, update, delete)
        """
        # Формируем update_data (вне блокировки для минимизации времени lock)
        update_data = self._build_update_data_from_api(sheet_name, api_response, action)

        # Блокируем только момент записи
        if update_data:
            await self.update_row_async(sheet_name, row_index, update_data)

    def save(self):
        """
        Сохранить изменения в Excel файл.
        """
        logger.info(f"Saving changes to Excel file: {self.excel_file}")

        try:
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
