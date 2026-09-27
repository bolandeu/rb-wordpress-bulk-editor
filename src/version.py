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

# Прежнее имя дистрибутива. Editable-установка `uv tool install -e` под ним
# исполняет этот же код из рабочего дерева; без запасного имени `cli_version`
# стал бы null, и antagor отказал бы как «wp-bulk старее 1.1.0». Убрать, когда
# на машинах не останется установок под старым именем.
_LEGACY_PACKAGE_NAMES = ("wp-bulk-editor",)


def installed_version() -> Optional[str]:
    """
    Версия установленного пакета или None при запуске из исходников.

    Голый запуск из репозитория (пакет не установлен) версии не имеет —
    проверку совместимости в этом случае пропускаем, а не проваливаем.
    """
    for name in (PACKAGE_NAME, *_LEGACY_PACKAGE_NAMES):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return None
