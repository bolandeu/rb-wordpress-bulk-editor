"""
Credentials: OS keychain (`keyring`) + переменные окружения.

Credential — это небольшой набор именованных полей, который хранится одним
JSON-блобом на каждого `provider`. Для этого CLI `provider` — домен сайта
WordPress (у каждого сайта свой логин), а поля — `username` + `password`.

Приоритет при чтении: переменные окружения перекрывают keychain по полям,
чтобы CI мог подменить один пароль, не трогая остальное. Секреты никогда
не пишутся в файлы проекта.

Имена переменных окружения:
- общие:      WP_API_USER / WP_API_TOKEN
- для сайта:  WP_<DOMAIN>_API_USER / WP_<DOMAIN>_API_TOKEN,
              где <DOMAIN> — домен в верхнем регистре, все не-буквенно-цифровые
              символы заменены на `_` (example.com → WP_EXAMPLE_COM_API_USER)
"""

import json
import os
import re
from typing import Dict, List, Mapping, Optional

import keyring
import keyring.errors
from keyring.backends import fail
from loguru import logger

from ..exceptions import CredentialStoreUnavailable

_SERVICE_PREFIX = "wp-bulk"

# Поля credential для WordPress-сайта.
CREDENTIAL_FIELDS = ("username", "password")

# Общие (не привязанные к сайту) переменные окружения для каждого поля.
_GLOBAL_ENV_VARS = {
    "username": "WP_API_USER",
    "password": "WP_API_TOKEN",
}


def _service_name(provider: str) -> str:
    return f"{_SERVICE_PREFIX}:{provider}"


def _provider_slug(provider: str) -> str:
    """Домен → фрагмент имени переменной окружения (example.com → EXAMPLE_COM)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", provider).strip("_").upper()


def env_var_names(provider: str, field: str) -> List[str]:
    """Имена переменных окружения для поля: сначала специфичная для сайта, потом общая."""
    names = []
    slug = _provider_slug(provider)
    global_name = _GLOBAL_ENV_VARS.get(field, f"WP_{field.upper()}")
    if slug:
        names.append(f"WP_{slug}_{global_name.removeprefix('WP_')}")
    names.append(global_name)
    return names


def read_fields_from_env(
    provider: str,
    fields: Optional[List[str]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """
    Прочитать те поля, что заданы переменными окружения.

    `environ` позволяет передать снимок окружения (например, сделанный до
    загрузки .env) вместо текущего `os.environ`.
    """
    source = os.environ if environ is None else environ
    result: Dict[str, str] = {}
    for field in fields or list(CREDENTIAL_FIELDS):
        for name in env_var_names(provider, field):
            value = source.get(name)
            if value:
                result[field] = value.strip()
                break
    return result


def get_credential(
    provider: str,
    env_fields: Optional[List[str]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, str]]:
    """
    Собрать credential для провайдера: keychain + переменные окружения поверх него.

    Returns:
        Словарь полей или None, если ничего не найдено.
    """
    try:
        stored_raw = keyring.get_password(_service_name(provider), provider)
    except keyring.errors.KeyringError as e:
        # Недоступный keychain (headless Linux, CI) не должен ломать env-путь.
        logger.warning(f"OS keychain недоступен ({e}); используются только переменные окружения")
        stored_raw = None

    stored: Dict[str, str] = json.loads(stored_raw) if stored_raw else {}
    fields = env_fields or list(stored.keys()) or list(CREDENTIAL_FIELDS)
    merged = {**stored, **read_fields_from_env(provider, fields, environ=environ)}
    return merged or None


def store_backend() -> Optional[str]:
    """
    Имя активного backend'а keyring или None, если пригодного нет.

    На машине без secret service `keyring` подставляет backend `fail`:
    запись и чтение падают, credentials можно передать только через env.
    """
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        return None
    return f"{type(backend).__module__}.{type(backend).__name__}"


def store_credential(provider: str, fields: Dict[str, str]) -> None:
    """Сохранить набор полей в OS keychain."""
    try:
        keyring.set_password(_service_name(provider), provider, json.dumps(fields))
    except keyring.errors.KeyringError as e:
        raise CredentialStoreUnavailable(
            f"Не удалось записать credentials для '{provider}' в OS keychain: {e}"
        )
    logger.debug(f"Credentials сохранены в keychain для {provider}: {sorted(fields)}")


def delete_credential(provider: str) -> None:
    """Удалить credential из keychain. Отсутствующий credential — не ошибка."""
    try:
        keyring.delete_password(_service_name(provider), provider)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as e:
        raise CredentialStoreUnavailable(
            f"Не удалось удалить credentials для '{provider}' из OS keychain: {e}"
        )
