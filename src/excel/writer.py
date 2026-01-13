"""
Запись данных обратно в Excel файл
"""

from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd
from openpyxl import load_workbook
from loguru import logger


class ExcelWriter:
    """
    Записывает данные обратно в Excel файл после синхронизации.
    Обновляет ID, даты, ссылки и другие поля.
    """

    def __init__(self, excel_file: Path):
        self.excel_file = excel_file
        self._workbook = None
        self._dataframes: Dict[str, pd.DataFrame] = {}

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
        update_data = {}

        # ID (для add операций)
        if action == "add" and "id" in api_response:
            update_data["id"] = api_response["id"]

        # Даты
        if "date" in api_response:
            update_data["date"] = api_response["date"]

        if "modified" in api_response:
            update_data["modified"] = api_response["modified"]

        # Ссылка
        if "link" in api_response:
            update_data["link"] = api_response["link"]

        # Slug
        if "slug" in api_response:
            update_data["slug"] = api_response["slug"]

        # Featured media
        if "featured_media" in api_response and api_response["featured_media"]:
            update_data["featured_media"] = api_response["featured_media"]

        # Image URL (кастомное поле из API)
        if "image_url" in api_response:
            update_data["image_url"] = api_response["image_url"]

        # После успешной операции меняем action на skip
        update_data["action"] = "skip"

        # Очищаем content_path и img_path если они были заполнены
        if sheet_name in self._dataframes:
            df = self._dataframes[sheet_name]
            if "content_path" in df.columns:
                update_data["content_path"] = ""
            if "img_path" in df.columns:
                update_data["img_path"] = ""

        # Обновляем строку
        if update_data:
            self.update_row(sheet_name, row_index, update_data)

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
