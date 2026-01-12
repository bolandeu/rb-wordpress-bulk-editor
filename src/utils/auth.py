"""
Утилиты для загрузки credentials из .env файлов
"""

import os
from pathlib import Path
from typing import Tuple, Optional
from dotenv import load_dotenv
from loguru import logger


def load_credentials_from_env(
    env_file: Optional[Path] = None,
    domain_override: Optional[str] = None,
    username_override: Optional[str] = None,
    password_override: Optional[str] = None
) -> Tuple[str, str, str]:
    """
    Загрузить credentials из .env файла или CLI параметров.

    Приоритет:
    1. CLI параметры (если указаны)
    2. .env файл (default .env или кастомный --env-file)

    Args:
        env_file: Путь к кастомному .env файлу (если None - используется default .env)
        domain_override: Переопределить domain из CLI
        username_override: Переопределить username из CLI
        password_override: Переопределить password из CLI

    Returns:
        (domain, username, password)

    Raises:
        ValueError: Если credentials не найдены
    """
    # Загружаем .env файл
    if env_file:
        if not env_file.exists():
            raise ValueError(f".env файл не найден: {env_file}")
        logger.debug(f"Загрузка credentials из: {env_file}")
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        logger.debug("Загрузка credentials из .env (default)")
        load_dotenv(override=True)

    # Получаем значения (приоритет у CLI параметров)
    domain = domain_override or os.getenv("WP_DOMAIN")
    username = username_override or os.getenv("WP_API_USER")
    password = password_override or os.getenv("WP_API_TOKEN")

    # Валидация
    missing = []
    if not domain:
        missing.append("WP_DOMAIN (или --domain)")
    if not username:
        missing.append("WP_API_USER (или --username)")
    if not password:
        missing.append("WP_API_TOKEN (или --password)")

    if missing:
        raise ValueError(
            f"Отсутствуют обязательные параметры: {', '.join(missing)}\n"
            f"Укажите их в .env файле или через параметры командной строки."
        )

    logger.debug(f"Credentials загружены: domain={domain}, user={username}")
    return domain, username, password
