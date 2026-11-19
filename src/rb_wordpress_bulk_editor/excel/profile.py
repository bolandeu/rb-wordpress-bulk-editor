"""
Чтение credentials из листа profile в Excel
"""

from pathlib import Path
import pandas as pd
from typing import Tuple

from ..exceptions import ProfileSheetMissingError, ExcelStructureError


class ProfileReader:
    """Читает credentials из листа 'profile' в Excel файле"""

    REQUIRED_COLUMNS = ["DOMAIN", "API_USER", "API_TOKEN"]

    def __init__(self, excel_file: Path):
        self.excel_file = excel_file

    def read(self) -> Tuple[str, str, str]:
        """
        Читает credentials из листа profile.

        Returns:
            Tuple[domain, username, password]

        Raises:
            ProfileSheetMissingError: Если лист profile не найден
            ExcelStructureError: Если структура листа неверная
        """
        try:
            # Читаем лист profile
            df = pd.read_excel(
                self.excel_file,
                sheet_name="profile",
                dtype=str,  # Все как строки
            )
        except ValueError as e:
            if "Worksheet named 'profile' not found" in str(e):
                raise ProfileSheetMissingError(f"Лист 'profile' не найден в файле {self.excel_file}")
            raise

        # Проверяем наличие обязательных колонок
        missing_columns = set(self.REQUIRED_COLUMNS) - set(df.columns)
        if missing_columns:
            raise ExcelStructureError(f"В листе 'profile' отсутствуют колонки: {', '.join(missing_columns)}")

        # Проверяем что есть хотя бы одна строка
        if len(df) == 0:
            raise ExcelStructureError("Лист 'profile' пустой. Добавьте строку с credentials.")

        # Берем первую строку
        first_row = df.iloc[0]

        domain = str(first_row["DOMAIN"]).strip()
        username = str(first_row["API_USER"]).strip()
        password = str(first_row["API_TOKEN"]).strip()

        # Валидация
        if not domain or domain == "nan":
            raise ExcelStructureError("DOMAIN не может быть пустым")

        if not username or username == "nan":
            raise ExcelStructureError("API_USER не может быть пустым")

        if not password or password == "nan":  # noqa: S105 — пустая ячейка pandas, не пароль
            raise ExcelStructureError("API_TOKEN не может быть пустым")

        return domain, username, password
