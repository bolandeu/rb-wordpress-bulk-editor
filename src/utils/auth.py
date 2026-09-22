"""
Утилиты для загрузки credentials.

Порядок разрешения (протокол cli-builder): CLI-флаг → переменная окружения →
OS keychain. Файл `.env` остаётся последним, legacy-слоем: он ещё работает для
существующих проектов, но новый путь секреты в файлах проекта не требует —
`wp-bulk` берёт их из keychain (`src/credentials/manager.py`) или из окружения.
"""

import os
from pathlib import Path
from typing import Tuple, Optional
from dotenv import load_dotenv
from loguru import logger

from ..exceptions import AuthenticationError, ConfigurationError
from ..credentials.manager import CREDENTIAL_FIELDS, get_credential


def load_credentials_from_env(
    env_file: Optional[Path] = None,
    domain_override: Optional[str] = None,
    username_override: Optional[str] = None,
    password_override: Optional[str] = None
) -> Tuple[str, str, str]:
    """
    Разрешить credentials WordPress.

    Приоритет:
    1. CLI параметры (--domain/--username/--password)
    2. Переменные окружения (WP_DOMAIN, WP_API_USER/WP_API_TOKEN,
       либо их версии для конкретного сайта — WP_<DOMAIN>_API_USER и т.д.)
    3. OS keychain (provider = домен сайта)
    4. .env файл — legacy, только если выше ничего не нашлось

    Args:
        env_file: Путь к кастомному .env файлу (если None - используется default .env)
        domain_override: Переопределить domain из CLI
        username_override: Переопределить username из CLI
        password_override: Переопределить password из CLI

    Returns:
        (domain, username, password)

    Raises:
        ConfigurationError: Если указанный .env файл не найден
        AuthenticationError: Если credentials не найдены ни в одном источнике
    """
    # Снимок окружения процесса ДО загрузки .env: так значения из .env
    # не маскируются под «переменную окружения» и честно остаются последними.
    process_env = dict(os.environ)

    # .env всё ещё загружается: оттуда берутся не-секретные настройки
    # (WP_VERIFY_SSL, таймауты, WP_TAXONOMIES...), которые читает Settings().
    if env_file:
        if not env_file.exists():
            raise ConfigurationError(f".env файл не найден: {env_file}")
        logger.debug(f"Загрузка .env: {env_file}")
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        logger.debug("Загрузка .env (default)")
        load_dotenv(override=True)

    domain = (domain_override or process_env.get("WP_DOMAIN") or os.getenv("WP_DOMAIN") or "").strip()

    # Окружение + keychain для этого домена (окружение перекрывает keychain).
    resolved = {}
    if domain:
        resolved = get_credential(
            domain, env_fields=list(CREDENTIAL_FIELDS), environ=process_env
        ) or {}

    username = (username_override or resolved.get("username") or "").strip()
    password = (password_override or resolved.get("password") or "").strip()

    # Legacy-слой: значения из .env файла проекта.
    if not username:
        username = (os.getenv("WP_API_USER") or "").strip()
        if username:
            logger.warning(
                "WP_API_USER взят из .env — перенесите credentials в keychain "
                "или переменные окружения, файлы проекта для секретов устарели"
            )
    if not password:
        password = (os.getenv("WP_API_TOKEN") or "").strip()
        if password:
            logger.warning(
                "WP_API_TOKEN взят из .env — перенесите credentials в keychain "
                "или переменные окружения, файлы проекта для секретов устарели"
            )

    # Валидация
    missing = []
    if not domain:
        missing.append("WP_DOMAIN (или --domain)")
    if not username:
        missing.append("WP_API_USER (или --username)")
    if not password:
        missing.append("WP_API_TOKEN (или --password)")

    if missing:
        raise AuthenticationError(
            f"Отсутствуют обязательные параметры: {', '.join(missing)}. "
            f"Укажите их флагами командной строки, переменными окружения "
            f"или сохраните в OS keychain."
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
            raise ConfigurationError(f".env файл не найден: {env_file}")
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
