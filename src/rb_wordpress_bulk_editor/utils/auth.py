"""
Утилиты для загрузки credentials.

Порядок разрешения (протокол cli-builder): CLI-флаг → переменная окружения →
OS keychain. Файл `.env` остаётся последним, legacy-слоем: он ещё работает для
существующих проектов, но новый путь секреты в файлах проекта не требует —
`wp-bulk` берёт их из keychain (`src/rb_wordpress_bulk_editor/credentials/manager.py`) или из окружения.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Tuple, Optional
from dotenv import find_dotenv, load_dotenv
from loguru import logger

from ..exceptions import AuthMissing, AuthenticationError, ConfigurationError
from ..credentials.manager import CREDENTIAL_FIELDS, get_credential, read_fields_from_env

# Legacy-имена переменных в .env проекта (последний слой цепочки).
_LEGACY_ENV_VARS = {"username": "WP_API_USER", "password": "WP_API_TOKEN"}

# Запасные имена из соглашения antagor (`data/projects/<name>/.env`): один и тот же
# .env проекта должен работать и при вызове wp-bulk из antagor, и вручную из
# терминала. Своё имя (WP_*) всегда главнее, алиас — только если его нет.
_ANTAGOR_ENV_ALIASES = {
    "domain": "SITE_DOMAIN",
    "username": "SITE_REST_API_USER",
    "password": "SITE_REST_API_PASSWORD",
}


def _env_names(field_name: str, own_name: str, environ: Dict[str, str]) -> Tuple[str, ...]:
    """Имена переменной поля по приоритету: своё, затем алиас antagor.

    Алиас учитывается, только если `SITE_CMS` не задан или равен `wordpress`:
    доступы от сайта на другой CMS подхватывать нельзя.
    """
    cms = (environ.get("SITE_CMS") or "wordpress").strip().lower()
    if cms != "wordpress":
        return (own_name,)
    return (own_name, _ANTAGOR_ENV_ALIASES[field_name])


def _first_set(names: Tuple[str, ...], environ: Dict[str, str]) -> Tuple[str, str]:
    """(имя, значение) первой непустой переменной из `names`; ("", "") — ни одной."""
    for name in names:
        value = environ.get(name)
        if value and value.strip():
            return name, value.strip()
    return "", ""


@dataclass
class ResolvedCredentials:
    """Результат разрешения credentials плюс источник каждого поля."""

    domain: str = ""
    username: str = ""
    password: str = ""
    # field → "cli" | "env" | "keychain" | ".env" | None (не найдено)
    sources: Dict[str, Optional[str]] = field(default_factory=dict)

    @property
    def missing(self) -> list:
        """Поля, которые не удалось разрешить."""
        return [name for name in ("domain", "username", "password") if not getattr(self, name)]


def resolve_credentials(
    env_file: Optional[Path] = None,
    domain_override: Optional[str] = None,
    username_override: Optional[str] = None,
    password_override: Optional[str] = None,
) -> ResolvedCredentials:
    """
    Пройти цепочку разрешения credentials и запомнить, откуда взято каждое поле.

    Единственная реализация цепочки: на неё опираются и реальные команды
    (`load_credentials_from_env`), и `doctor`. Разные реализации разъезжаются —
    diagnostics обязаны видеть ровно то, что увидит sync.

    Приоритет:
    1. CLI параметры (--domain/--username/--password)
    2. Переменные окружения (WP_DOMAIN, WP_API_USER/WP_API_TOKEN,
       либо их версии для конкретного сайта — WP_<DOMAIN>_API_USER и т.д.;
       домен также из SITE_DOMAIN — алиас antagor)
    3. OS keychain (provider = домен сайта)
    4. .env файл — legacy, только если выше ничего не нашлось
       (WP_DOMAIN/WP_API_USER/WP_API_TOKEN либо алиасы antagor
       SITE_DOMAIN/SITE_REST_API_USER/SITE_REST_API_PASSWORD)

    Ничего не найдено — не ошибка: read-only операции работают анонимно,
    а решение «хватает или нет» принимает вызывающий.

    Raises:
        ConfigurationError: Если указанный .env файл не найден
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
        # usecwd=True обязателен: без него find_dotenv ищет не от текущей папки
        # пользователя, а от файла вызывающего кода (в установленном пакете —
        # site-packages), и `.env` рядом с пользователем просто не находится.
        logger.debug("Загрузка .env (поиск от текущей папки)")
        load_dotenv(dotenv_path=find_dotenv(usecwd=True), override=True)

    sources: Dict[str, Optional[str]] = {}

    # Окружение после load_dotenv: процесс + .env (для последнего, legacy-слоя).
    dotenv_env = dict(os.environ)
    domain_names = _env_names("domain", "WP_DOMAIN", dotenv_env)
    _, domain_from_env = _first_set(domain_names, process_env)
    _, domain_from_dotenv = _first_set(domain_names, dotenv_env)
    if domain_override:
        domain, sources["domain"] = domain_override.strip(), "cli"
    elif domain_from_env:
        domain, sources["domain"] = domain_from_env, "env"
    elif domain_from_dotenv:
        domain, sources["domain"] = domain_from_dotenv, ".env"
    else:
        domain, sources["domain"] = "", None

    # Окружение + keychain для этого домена (окружение перекрывает keychain).
    from_env: Dict[str, str] = {}
    from_keychain: Dict[str, str] = {}
    if domain:
        from_env = read_fields_from_env(domain, list(CREDENTIAL_FIELDS), environ=process_env)
        merged = get_credential(domain, env_fields=list(CREDENTIAL_FIELDS), environ=process_env) or {}
        from_keychain = {k: v for k, v in merged.items() if k not in from_env}

    overrides = {"username": username_override, "password": password_override}
    values: Dict[str, str] = {}
    for name in CREDENTIAL_FIELDS:
        legacy_var, legacy_value = _first_set(_env_names(name, _LEGACY_ENV_VARS[name], dotenv_env), dotenv_env)
        override = overrides[name]
        if override:
            values[name], sources[name] = override.strip(), "cli"
        elif from_env.get(name):
            values[name], sources[name] = from_env[name].strip(), "env"
        elif from_keychain.get(name):
            values[name], sources[name] = from_keychain[name].strip(), "keychain"
        elif legacy_value:
            values[name], sources[name] = legacy_value, ".env"
            logger.warning(
                f"{legacy_var} взят из .env — перенесите credentials в keychain "
                "или переменные окружения, файлы проекта для секретов устарели"
            )
        else:
            values[name], sources[name] = "", None

    return ResolvedCredentials(
        domain=domain,
        username=values["username"],
        password=values["password"],
        sources=sources,
    )


def load_credentials_from_env(
    env_file: Optional[Path] = None,
    domain_override: Optional[str] = None,
    username_override: Optional[str] = None,
    password_override: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Разрешить credentials WordPress (см. `resolve_credentials`) и потребовать их наличия.

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
    resolved = resolve_credentials(
        env_file=env_file,
        domain_override=domain_override,
        username_override=username_override,
        password_override=password_override,
    )
    domain, username, password = resolved.domain, resolved.username, resolved.password

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
    env_file: Optional[Path] = None, cli_concurrency: Optional[int] = None, cli_max_retries: Optional[int] = None
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
        logger.debug("Загрузка async config из .env (поиск от текущей папки)")
        load_dotenv(dotenv_path=find_dotenv(usecwd=True), override=True)

    # Создаем Settings (читает из .env с префиксом WP_)
    settings = Settings()

    # Приоритет: CLI > .env > default
    concurrency = cli_concurrency or settings.sync_concurrency
    max_retries = cli_max_retries or settings.max_retries

    # Создаем RetryConfig
    retry_config = RetryConfig(max_retries=max_retries, base_delay=settings.retry_delay)

    # Создаем CircuitBreaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=settings.circuit_breaker_threshold, recovery_timeout=settings.circuit_breaker_recovery
    )

    logger.debug(
        f"Async config загружен: concurrency={concurrency}, "
        f"max_retries={max_retries}, "
        f"circuit_breaker_threshold={settings.circuit_breaker_threshold}"
    )

    return concurrency, retry_config, circuit_breaker


# Доступы WP-адаптера (`post`, `media attach`): одна пара на вызов — сайт
# называет `--site`, логин и пароль подставляет вызывающий (Windmill из секрета
# сайта). Нарочно не цепочка `resolve_credentials`: на сервере нет keychain, а
# флаг `--password` оставил бы пароль в argv и истории процессов.
ADAPTER_ENV_VARS = (_LEGACY_ENV_VARS["username"], _LEGACY_ENV_VARS["password"])


def adapter_credentials(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, str]:
    """
    (username, password) только из окружения `WP_API_USER` / `WP_API_TOKEN`.

    Raises:
        AuthMissing: Не задана хотя бы одна переменная (сеть не трогается).
    """
    source = os.environ if environ is None else environ
    values = [(source.get(name) or "").strip() for name in ADAPTER_ENV_VARS]
    missing = [name for name, value in zip(ADAPTER_ENV_VARS, values) if not value]
    if missing:
        raise AuthMissing(missing, list(ADAPTER_ENV_VARS))
    return values[0], values[1]
