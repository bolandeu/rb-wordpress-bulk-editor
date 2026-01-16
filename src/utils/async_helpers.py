"""
Async helpers для параллельной обработки строк.

Включает:
- ProcessResult - результат обработки одной строки
- AsyncBatchProcessor - параллельная обработка с Semaphore
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Callable, Dict
import pandas as pd
from loguru import logger


@dataclass
class ProcessResult:
    """
    Результат обработки одной строки.

    Attributes:
        success: True если обработка успешна, False если ошибка
        action: Тип операции ("added", "updated", "deleted")
        row_index: Индекс строки в DataFrame
        api_response: Ответ от WordPress API (если success=True)
        error: Исключение (если success=False)
    """
    success: bool
    action: str
    row_index: int
    api_response: Optional[Dict[str, Any]] = None
    error: Optional[Exception] = None


class AsyncBatchProcessor:
    """
    Helper для параллельной обработки строк с Semaphore.

    Обеспечивает:
    - Ограничение параллельных запросов (через Semaphore)
    - Обработку ошибок (ошибки не прерывают общий процесс)
    - Сбор результатов в структурированном виде

    Использование:
        processor = AsyncBatchProcessor(concurrency=5)
        tasks = [
            processor.process_row("add", idx, row, my_add_func)
            for idx, row in df.iterrows()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    Attributes:
        semaphore: asyncio.Semaphore для ограничения concurrency
    """

    def __init__(self, concurrency: int):
        """
        Инициализация processor.

        Args:
            concurrency: Максимальное количество одновременных операций
        """
        self.semaphore = asyncio.Semaphore(concurrency)
        logger.debug(f"AsyncBatchProcessor initialized with concurrency={concurrency}")

    async def process_row(
        self,
        action: str,
        row_index: int,
        row: pd.Series,
        process_func: Callable,
        *args,
        **kwargs
    ) -> ProcessResult:
        """
        Обработать одну строку с Semaphore.

        Метод:
        1. Захватывает semaphore (ждет если достигнут лимит)
        2. Вызывает process_func с переданными аргументами
        3. Обрабатывает результат или ошибку
        4. Освобождает semaphore
        5. Возвращает ProcessResult

        Args:
            action: Тип операции ("add", "update", "delete")
            row_index: Индекс строки в DataFrame
            row: Данные строки (pandas Series) - для логирования, не передаётся в func
            process_func: Async функция обработки (partial с уже привязанными аргументами)
                          Signature: async def func() -> Dict
            *args: Дополнительные позиционные аргументы для process_func
            **kwargs: Дополнительные именованные аргументы для process_func

        Returns:
            ProcessResult с результатом обработки

        Примечание:
            Ошибки НЕ пробрасываются наружу, а записываются в ProcessResult.error
            Это позволяет обрабатывать ошибки в одной строке без прерывания всего batch
        """
        async with self.semaphore:
            try:
                # Вызываем функцию обработки (partial уже содержит все аргументы)
                api_response = await process_func(*args, **kwargs)

                # Успешная обработка
                logger.debug(f"Row {row_index} ({action}): success")

                return ProcessResult(
                    success=True,
                    action=action,
                    row_index=row_index,
                    api_response=api_response
                )

            except Exception as e:
                # Ошибка при обработке
                logger.error(f"Row {row_index} ({action}): {e}")

                return ProcessResult(
                    success=False,
                    action=action,
                    row_index=row_index,
                    error=e
                )
