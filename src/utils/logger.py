"""
Настройка логирования с loguru
"""

from pathlib import Path
from datetime import datetime
import sys
from typing import Optional, TextIO
from loguru import logger

from ..config import LoggingConfig
from ..constants import LOG_DATE_FORMAT


def setup_logger(
    logs_folder: Path, config: LoggingConfig, command_name: str = "sync", console_stream: Optional[TextIO] = None
) -> Path:
    """
    Настроить loguru logger.

    Args:
        logs_folder: Папка для логов
        config: Конфигурация логирования
        command_name: Название команды (sync, fetch, options)
        console_stream: Поток для консольного вывода (по умолчанию stdout;
            в JSON-режиме команды передают stderr, чтобы не пачкать данные)

    Returns:
        Path к созданному лог-файлу
    """
    # Создаем папку для логов
    logs_folder.mkdir(parents=True, exist_ok=True)

    # Генерируем имя файла
    timestamp = datetime.now().strftime(LOG_DATE_FORMAT)
    log_file = logs_folder / f"{command_name}_{timestamp}.log"

    # Удаляем стандартные обработчики
    logger.remove()

    # Консольный вывод (цветной, упрощенный)
    logger.add(
        console_stream or sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=config.level,
        colorize=True,
    )

    # Файловый вывод (детальный)
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG",  # В файл пишем всё
        rotation=config.rotation,
        retention=config.retention,
        encoding="utf-8",
    )

    logger.info(f"Лог-файл: {log_file}")

    return log_file


def log_summary(stats: dict, log_file: Path):
    """
    Вывести summary статистику в лог.

    Args:
        stats: Словарь со статистикой
        log_file: Путь к лог-файлу
    """
    import json

    logger.info("=" * 50)
    logger.info("SYNC SUMMARY")
    logger.info("=" * 50)

    if "file" in stats:
        logger.info(f"File: {stats['file']}")

    if "started_at" in stats and "finished_at" in stats:
        started = datetime.fromisoformat(stats["started_at"])
        finished = datetime.fromisoformat(stats["finished_at"])
        duration = finished - started
        logger.info(f"Started: {stats['started_at']}")
        logger.info(f"Finished: {stats['finished_at']}")
        logger.info(f"Duration: {duration}")

    # Статистика по листам
    if "sheets" in stats:
        for sheet_name, sheet_stats in stats["sheets"].items():
            logger.info("")
            logger.info(f"Sheet: {sheet_name}")
            logger.info(f"  Total rows: {sheet_stats.get('total', 0)}")
            logger.info(f"  ✓ Added: {sheet_stats.get('added', 0)}")
            logger.info(f"  ✓ Updated: {sheet_stats.get('updated', 0)}")
            logger.info(f"  ✓ Deleted: {sheet_stats.get('deleted', 0)}")
            logger.info(f"  ⊘ Skipped: {sheet_stats.get('skipped', 0)}")
            logger.info(f"  ✗ Errors: {sheet_stats.get('errors', 0)}")

    # Общая статистика
    if "overall" in stats:
        overall = stats["overall"]
        logger.info("")
        logger.info("Overall:")
        logger.info(f"  Total rows processed: {overall.get('total', 0)}")
        logger.info(f"  Successful operations: {overall.get('successful', 0)}")
        logger.info(f"  Errors: {overall.get('errors', 0)}")

        success_rate = overall.get("success_rate", 0)
        logger.info(f"  Success rate: {success_rate:.2f}%")

    logger.info("=" * 50)

    # Сохраняем JSON версию рядом с логом
    json_file = log_file.with_suffix(".json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    logger.info(f"JSON статистика: {json_file}")
