"""
Данные, которые едут внутри пакета (см. [tool.setuptools.package-data]).

`SKILL.md` — соглашения CLI для агента. Единственная копия: её печатает
`wp-bulk --ai`, её же читает агент из установленного пакета.
"""

from importlib import resources

SKILL_FILENAME = "SKILL.md"


def read_skill_doc() -> str:
    """Содержимое бандленного SKILL.md."""
    return resources.files(__name__).joinpath(SKILL_FILENAME).read_text(encoding="utf-8")
