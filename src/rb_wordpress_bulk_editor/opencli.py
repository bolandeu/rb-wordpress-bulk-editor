"""`describe --json`: этот CLI как документ OpenCLI (https://opencli.org, spec 0.1).

Машиночитаемый контракт инструмента для архитектора, Windmill и каталога
инструментов. Порт `core/opencli.py` шаблона cli-generator: команды, опции и
аргументы берутся из дерева Click, которое строит Typer, — ничего не пишется
дважды. Читается утиной типизацией (`param_type_name`, `commands`): свежий
Typer везёт свою копию Click, и `isinstance` против пакета `click` не совпал бы.
Тип и значение по умолчанию параметра в OpenCLI полей не имеют — они уходят в
`metadata` с именами `x-rb-*`; на корневой команде — переменные окружения,
данные и имя дистрибутива.

Отличия от шаблона (wp-bulk на шаблоне не сгенерирован): коды выхода —
`exceptions.EXIT_CODES`, переменные окружения — таблица `ENV_VARS` ниже,
данные — каталог логов (своей базы у инструмента нет).

Документ — межинструментальный контракт, как `auth status`: tests/test_describe.py
проверяет его по схеме OpenCLI и снапшотом. Изменение или удаление чего-либо в
нём — ломающее изменение.
"""

from __future__ import annotations

import enum
import inspect
import json
from importlib import metadata
from pathlib import PurePath
from typing import Any

from .cli.project import LOG_APPNAME
from .exceptions import EXIT_CODES
from .output import CLI_NAME
from .version import PACKAGE_NAME, installed_version

OPENCLI_VERSION = "0.1"
DEV_VERSION = "0.0.0-dev"
TOOL = "wp_bulk"
_IMPORT_PACKAGE = __name__.split(".")[0]

# Все переменные окружения, которые читает инструмент.
ENV_VARS: list[dict[str, Any]] = [
    {
        "name": "WP_API_USER",
        "purpose": "credential",
        "provider": "wordpress",
        "field": "username",
        "required": True,
        "description": "Логин WordPress. Единственный источник для post upsert/get и media attach; "
        "для остальных команд — после WP_<DOMAIN>_API_USER",
        "fallbacks": ["SITE_REST_API_USER"],
    },
    {
        "name": "WP_API_TOKEN",
        "purpose": "credential",
        "provider": "wordpress",
        "field": "password",
        "required": True,
        "description": "Application Password WordPress. Единственный источник для post upsert/get и "
        "media attach; для остальных команд — после WP_<DOMAIN>_API_TOKEN",
        "fallbacks": ["SITE_REST_API_PASSWORD"],
    },
    {
        "name": "WP_<DOMAIN>_API_USER",
        "purpose": "credential",
        "provider": "wordpress",
        "field": "username",
        "required": False,
        "description": "Логин для сайта <DOMAIN> (домен в верхнем регистре, не-буквы → _); кроме post/media attach",
    },
    {
        "name": "WP_<DOMAIN>_API_TOKEN",
        "purpose": "credential",
        "provider": "wordpress",
        "field": "password",
        "required": False,
        "description": "Application Password для сайта <DOMAIN>; кроме post/media attach",
    },
    {
        "name": "WP_DOMAIN",
        "purpose": "config",
        "required": False,
        "description": "Сайт для команд с файлом данных (у post/media attach — флаг --site)",
        "fallbacks": ["SITE_DOMAIN"],
    },
    {"name": "WP_PROJECT", "purpose": "config", "required": False, "description": "Папка проекта (как --project)"},
    {
        "name": "WP_VERIFY_SSL",
        "purpose": "config",
        "required": False,
        "description": "false — не проверять сертификат сайта (локальный WordPress)",
    },
]


def document(root: Any) -> dict:
    """Документ OpenCLI для CLI с корневой командой `root`."""
    command = _command(CLI_NAME, root)
    command.pop("description", None)
    command["exitCodes"] = [{"code": code, "description": meaning} for code, meaning in EXIT_CODES.items()]
    command["metadata"] = _meta(
        {
            "x-rb-tool": {"tool": TOOL, "dist": PACKAGE_NAME, "package": _IMPORT_PACKAGE},
            "x-rb-env": ENV_VARS,
            "x-rb-data": data(),
        }
    )
    info = {"title": CLI_NAME, "version": installed_version() or DEV_VERSION}
    if summary := _summary():
        info["summary"] = summary
    return {"opencli": OPENCLI_VERSION, "info": info, "command": command}


def data() -> dict:
    """Файлы инструмента вне проекта: логи команд без проекта (platformdirs)."""
    return {
        "user_log_dir": {
            "appname": LOG_APPNAME,
            "files": [
                {
                    "path": "<команда>_<время>.log",
                    "kind": "log",
                    "description": "Лог команды, запущенной без проекта (с проектом — <проект>/logs); "
                    "post upsert/get и media attach файлов не пишут — только stderr",
                }
            ],
        }
    }


def _summary() -> str | None:
    try:
        return metadata.metadata(PACKAGE_NAME)["Summary"]
    except (metadata.PackageNotFoundError, KeyError):
        return None


def _command(name: str, cmd: Any) -> dict:
    out: dict[str, Any] = {"name": name}
    if description := _text(cmd.help):
        out["description"] = description
    if cmd.hidden:
        out["hidden"] = True
    options = [_option(p) for p in cmd.params if p.param_type_name == "option"]
    arguments = [_argument(p) for p in cmd.params if p.param_type_name == "argument"]
    if options:
        out["options"] = options
    if arguments:
        out["arguments"] = arguments
    if (subcommands := getattr(cmd, "commands", None)) is not None:  # группа
        # Порядок регистрации (Typer его хранит) — тот же, что в --help.
        out["commands"] = [_command(sub_name, sub) for sub_name, sub in subcommands.items()]
    return out


def _option(param: Any) -> dict:
    names = sorted(param.opts, key=len, reverse=True)  # длинная форма — имя
    out: dict[str, Any] = {"name": names[0]}
    if aliases := [*names[1:], *param.secondary_opts]:
        out["aliases"] = aliases
    if param.required:
        out["required"] = True
    if not param.is_flag and not param.count:
        value = {"name": param.metavar or (param.name or "value").upper(), "required": True}
        if choices := _choices(param.type):
            value["acceptedValues"] = choices
        out["arguments"] = [value]
    if description := _text(param.help):
        out["description"] = description
    if param.hidden:
        out["hidden"] = True
    out["metadata"] = _param_meta(param, multiple=param.multiple)
    return out


def _argument(param: Any) -> dict:
    out: dict[str, Any] = {"name": param.name or "argument"}
    out["required"] = param.required
    maximum = None if param.nargs == -1 else param.nargs
    out["arity"] = {"minimum": param.nargs if param.required and param.nargs > 0 else int(param.required)}
    if maximum is not None:
        out["arity"]["maximum"] = maximum
    if choices := _choices(param.type):
        out["acceptedValues"] = choices
    if description := _text(getattr(param, "help", None)):  # у аргументов Typer есть help
        out["description"] = description
    if getattr(param, "hidden", False):
        out["hidden"] = True
    out["metadata"] = _param_meta(param, multiple=param.nargs == -1)
    return out


def _param_meta(param: Any, *, multiple: bool) -> list[dict]:
    default = param.default
    # x-rb-default is always there: the value, or null when there is none or it is computed at run time.
    no_default = default is None or callable(default) or default == ()
    meta: dict[str, Any] = {"x-rb-type": param.type.name, "x-rb-default": None if no_default else _jsonable(default)}
    if multiple:
        meta["x-rb-multiple"] = True
    return _meta(meta, keep_null=("x-rb-default",))


def _choices(param_type: Any) -> list[str] | None:
    choices = getattr(param_type, "choices", None)  # Choice (или Enum, который Typer превратил в Choice)
    return [str(_jsonable(choice)) for choice in choices] if choices else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _text(text: str | None) -> str:
    # output.guard экранирует `[` для rich-разметки Typer; документу нужен сам docstring.
    return inspect.cleandoc(text or "").replace("\\[", "[").strip()


def _meta(values: dict[str, Any], keep_null: tuple[str, ...] = ()) -> list[dict]:
    """Metadata entries; a null value is dropped unless `keep_null` names its key."""
    return [{"name": name, "value": value} for name, value in values.items() if value is not None or name in keep_null]
