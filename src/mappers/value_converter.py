"""
Конвертация значений полей для WordPress ACF API
"""

from typing import Any

import pandas as pd


_FALSY_STRINGS = {"0", "нет", "no", "false", "f", "n"}


def parse_bool_default_true(value: Any) -> bool:
    """Преобразовать значение Excel в bool для полей «по умолчанию true».

    Правила:
        - NaN / None / пустая строка → True
        - bool → как есть
        - число 0 → False, любое ненулевое → True
        - строка "0"/"нет"/"no"/"false"/"f"/"n" (регистр/пробелы игнорируются) → False
        - всё остальное (включая непонятные значения) → True
    """
    if pd.isna(value) or value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if not s:
        return True
    if s in _FALSY_STRINGS:
        return False
    return True


def convert_acf_value(value: Any) -> Any:
    """
    Конвертирует значение ACF поля в правильный тип.

    Строки, которые выглядят как числа, конвертируются в числа.
    Даты конвертируются в формат YYYYMMDD для ACF Date Picker.
    Это важно для WordPress ACF API.
    """
    from datetime import datetime

    if pd.isna(value):
        return None

    # Если уже число - вернуть как есть
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    # Если строка - попробовать сконвертировать
    if isinstance(value, str):
        value = value.strip()

        # Проверяем формат даты YYYY-MM-DD HH:MM:SS или YYYY-MM-DD
        date_formats = [
            '%Y-%m-%d %H:%M:%S',  # 2020-04-24 00:00:00
            '%Y-%m-%d',            # 2020-04-24
            '%d.%m.%Y %H:%M:%S',   # 24.04.2020 00:00:00
            '%d.%m.%Y',            # 24.04.2020
            '%d/%m/%Y',            # 24/04/2020
        ]

        for date_format in date_formats:
            try:
                dt = datetime.strptime(value, date_format)
                # ACF Date Picker ожидает формат YYYYMMDD
                return dt.strftime('%Y%m%d')
            except ValueError:
                continue

        # Проверяем формат даты YYYYMMDD (8 цифр, начинается с 19 или 20)
        # Не конвертируем в число, оставляем строкой
        if len(value) == 8 and value.isdigit() and value[:2] in ('19', '20'):
            return value

        # Пробуем как целое число
        try:
            return int(value)
        except ValueError:
            pass

        # Пробуем как float
        try:
            float_val = float(value)
            if float_val.is_integer():
                return int(float_val)
            return float_val
        except ValueError:
            pass

        # Булевы значения
        if value.lower() in ('true', 'yes', '1'):
            return True
        if value.lower() in ('false', 'no', '0'):
            return False

        # Возвращаем строку как есть
        return value

    return value
