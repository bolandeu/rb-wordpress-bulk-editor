"""
Чтение и запись настроек сайта из/в лист options
"""

from pathlib import Path
from typing import Dict, Any
import pandas as pd
from openpyxl import load_workbook, Workbook
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


class OptionsWriter:
    """
    Записывает настройки сайта в лист 'options' Excel файла.

    Формат листа:
    - 1 строка: заголовки (option, value)
    - Далее: данные (option_name, option_value)

    Пример:
    | option          | value         |
    |-----------------|---------------|
    | site_title      | My Website    |
    | posts_per_page  | 10            |
    | timezone        | Europe/Moscow |
    """

    def __init__(self, excel_file: Path):
        self.excel_file = excel_file

    def write(self, options: Dict[str, Any], mode: str = "replace") -> None:
        """
        Записывает настройки в лист 'options'.

        Args:
            options: Словарь с настройками {option_name: value}
            mode: Режим записи:
                - "replace": заменить все данные в листе
                - "append": добавить к существующим данным

        Raises:
            ExcelError: Если произошла ошибка записи
        """
        try:
            logger.debug(f"Запись {len(options)} настроек в лист 'options' файла {self.excel_file}")

            # Преобразуем словарь в DataFrame
            data = [{"option": key, "value": value} for key, value in options.items()]
            new_df = pd.DataFrame(data)

            # Если файл не существует, создаём новый
            if not self.excel_file.exists():
                logger.info(f"Создание нового файла {self.excel_file}")
                with pd.ExcelWriter(self.excel_file, engine="openpyxl") as writer:
                    new_df.to_excel(writer, sheet_name="options", index=False)
                logger.info(f"Записано {len(options)} настроек в новый файл")
                return

            # Файл существует - загружаем workbook
            wb = load_workbook(self.excel_file)

            # Проверяем существует ли лист 'options'
            if "options" in wb.sheetnames:
                if mode == "append":
                    # Читаем существующие данные
                    existing_df = pd.read_excel(
                        self.excel_file,
                        sheet_name="options",
                        header=0
                    )
                    # Объединяем с новыми данными
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    logger.debug(f"Добавлено {len(new_df)} строк к {len(existing_df)} существующим")
                    new_df = combined_df
                else:
                    logger.debug("Режим replace: заменяем все данные в листе 'options'")

                # Удаляем старый лист
                del wb["options"]

            # Создаём новый лист с данными
            ws = wb.create_sheet("options", 0)  # Добавляем в начало

            # Записываем заголовки
            ws.append(["option", "value"])

            # Записываем данные
            for _, row in new_df.iterrows():
                ws.append([row["option"], row["value"]])

            # Сохраняем файл
            wb.save(self.excel_file)
            logger.info(f"Записано {len(options)} настроек в лист 'options'")

        except Exception as e:
            raise ExcelError(f"Ошибка записи настроек в 'options': {e}")
