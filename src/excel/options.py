"""
Чтение настроек сайта из листа options
"""

from pathlib import Path
from typing import Dict, Any
import pandas as pd
from loguru import logger

from ..exceptions import ExcelError


class OptionsReader:
    """
    Читает настройки сайта из листа 'options' в Excel файле.

    Формат листа:
    - 1 строка: заголовки (пропускается)
    - 1 столбец: ключ (option name)
    - 2 столбец: значение (value)
    - 3+ столбцы: игнорируются

    Пример:
    | option          | value         |
    |-----------------|---------------|
    | site_title      | My Website    |
    | posts_per_page  | 10            |
    | timezone        | Europe/Moscow |
    """

    def __init__(self, excel_file: Path):
        self.excel_file = excel_file

    def read(self) -> Dict[str, Any]:
        """
        Читает настройки из листа 'options'.

        Returns:
            Словарь с настройками {option: value}
            Пустые значения НЕ включаются

        Raises:
            ExcelError: Если лист не найден или некорректный формат
        """
        try:
            logger.debug(f"Чтение листа 'options' из {self.excel_file}")

            # Читаем лист, пропуская первую строку (заголовки)
            df = pd.read_excel(
                self.excel_file,
                sheet_name="options",
                header=0,  # Первая строка - заголовки
                dtype=str  # Все как строки
            )

            # Проверяем что есть минимум 2 столбца
            if len(df.columns) < 2:
                raise ExcelError(
                    f"Лист 'options' должен содержать минимум 2 столбца (option, value)"
                )

            options = {}

            # Обрабатываем каждую строку
            for index, row in df.iterrows():
                # Берем первые два столбца
                option_name = row.iloc[0]
                option_value = row.iloc[1]

                # Пропускаем если ключ пустой
                if pd.isna(option_name) or str(option_name).strip() == "":
                    continue

                # Пропускаем если значение пустое
                if pd.isna(option_value) or str(option_value).strip() == "":
                    logger.debug(f"Пропуск опции '{option_name}' (пустое значение)")
                    continue

                option_name = str(option_name).strip()
                option_value = str(option_value).strip()

                # Пытаемся преобразовать в числа если возможно
                try:
                    # Пробуем int
                    if "." not in option_value:
                        option_value = int(option_value)
                    else:
                        # Пробуем float
                        option_value = float(option_value)
                except (ValueError, TypeError):
                    # Оставляем как строку
                    pass

                options[option_name] = option_value
                logger.debug(f"Опция: {option_name} = {option_value}")

            logger.info(f"Прочитано {len(options)} настроек из листа 'options'")
            return options

        except FileNotFoundError:
            raise ExcelError(f"Файл не найден: {self.excel_file}")
        except ValueError as e:
            if "Worksheet named 'options' not found" in str(e):
                raise ExcelError(
                    f"Лист 'options' не найден в файле {self.excel_file}"
                )
            raise ExcelError(f"Ошибка чтения листа 'options': {e}")
        except Exception as e:
            raise ExcelError(f"Неожиданная ошибка при чтении 'options': {e}")
