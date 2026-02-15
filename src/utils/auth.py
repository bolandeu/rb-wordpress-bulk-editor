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
    domain = (domain_override or os.getenv("WP_DOMAIN") or "").strip()
    username = (username_override or os.getenv("WP_API_USER") or "").strip()
    password = (password_override or os.getenv("WP_API_TOKEN") or "").strip()

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

    logger.debug(f"Credentials загружены: domain={domain}, user={username[:3]}***")
    return domain, username, password


def load_async_config_from_env(
    env_file: Optional[Path] = None,
    cli_concurrency: Optional[int] = None,
    cli_max_retries: Optional[int] = None
) -> Tuple:
    """
    Загрузить async конфигурацию из .env файла или CLI параметров.

    Приоритет:
    1. CLI параметры (если указаны)
    2. .env файл (default .env или кастомный --env-file)
    3. Default значения из constants.py

    Args:
        env_file: Путь к кастомному .env файлу (если None - используется default .env)
        cli_concurrency: Переопределить concurrency из CLI
        cli_max_retries: Переопределить max_retries из CLI

    Returns:
        (concurrency, retry_config, circuit_breaker)
        где:
        - concurrency: int - максимум параллельных запросов
        - retry_config: RetryConfig - конфигурация retry
        - circuit_breaker: CircuitBreaker - circuit breaker

    Example:
        >>> concurrency, retry_config, cb = load_async_config_from_env()
        >>> wp_client = WordPressClient(config, retry_config=retry_config, circuit_breaker=cb)
    """
    from ..config import Settings
    from .retry import RetryConfig, CircuitBreaker

    # Загружаем .env файл
    if env_file:
        if not env_file.exists():
            raise ValueError(f".env файл не найден: {env_file}")
        logger.debug(f"Загрузка async config из: {env_file}")
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        logger.debug("Загрузка async config из .env (default)")
        load_dotenv(override=True)

    # Создаем Settings (читает из .env с префиксом WP_)
    settings = Settings()

    # Приоритет: CLI > .env > default
    concurrency = cli_concurrency or settings.sync_concurrency
    max_retries = cli_max_retries or settings.max_retries

    # Создаем RetryConfig
    retry_config = RetryConfig(
        max_retries=max_retries,
        base_delay=settings.retry_delay
    )

    # Создаем CircuitBreaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=settings.circuit_breaker_threshold,
        recovery_timeout=settings.circuit_breaker_recovery
    )

    logger.debug(
        f"Async config загружен: concurrency={concurrency}, "
        f"max_retries={max_retries}, "
        f"circuit_breaker_threshold={settings.circuit_breaker_threshold}"
    )

    return concurrency, retry_config, circuit_breaker
