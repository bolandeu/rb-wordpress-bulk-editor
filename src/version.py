"""
Версия установленного пакета.

Отдельный модуль без зависимостей от CLI: версию читают и `cli` (--version,
проверка WP_BULK_MIN_VERSION), и `output` (поле `cli_version` в JSON-конверте).
"""

from importlib import metadata
from typing import Optional

# Имя дистрибутива (`[project] name`, стандарт §10: `rb-<tool>`) — для
# importlib.metadata и подсказки `uv tool upgrade`. Команда остаётся `wp-bulk`.
PACKAGE_NAME = "rb-wordpress-bulk-editor"


def installed_version() -> Optional[str]:
    """
    Версия установленного пакета или None при запуске из исходников.

    Голый запуск из репозитория (пакет не установлен) версии не имеет —
    проверку совместимости в этом случае пропускаем, а не проваливаем.
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None
