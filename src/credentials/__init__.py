"""
Хранилище credentials: OS keychain + переменные окружения, никогда — файлы проекта.
"""

from .manager import (
    CREDENTIAL_FIELDS,
    delete_credential,
    get_credential,
    read_fields_from_env,
    store_backend,
    store_credential,
)

__all__ = [
    "CREDENTIAL_FIELDS",
    "delete_credential",
    "get_credential",
    "read_fields_from_env",
    "store_backend",
    "store_credential",
]
