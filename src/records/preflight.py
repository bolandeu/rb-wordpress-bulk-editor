"""
Проверка набора данных перед отправкой в WordPress (preflight).

Работает на уровне `Record`, поэтому одна для Excel и JSON. Вызывается из
`validate` и автоматически перед `sync`/`async` — ошибки останавливают
прогон до подключения к сайту (отключается `--skip-checks`).

Что проверяется:
- имена полей: всё, кроме `acf.*`/`acf_*`/`meta.*`, должно быть известным
  полем WordPress, SEO-полем (`WP_SEO_FIELDS`) или своим полем проекта
  (`WP_EXTRA_FIELDS`); к опечатке подсказывается похожее имя. Опечатка
  в колонке молча уходит в payload, а WordPress её так же молча игнорирует;
- имена таксономий: колонки и записи-термины — в каноническом виде
  (строчные буквы, цифры, части через `_`/`-`), иначе slug не совпадёт с сайтом;
- значения записей, которые sync отправит: `id` у update/delete, `status`,
  дубли `slug`, разделитель терминов `"; "`, булевы `seo.robots.*`,
  значения-пути к файлам.

Записи с `skip`/пустым action на сайт не уходят: их значения не проверяются,
а проблемы с полями и файлами становятся предупреждениями.
"""

import difflib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..constants import (
    ACF_PREFIX,
    BOOL_FIELDS_DEFAULT_TRUE,
    SYSTEM_COLUMNS,
    TAXONOMY_DELIMITER,
    VALID_ACTIONS,
    is_taxonomy_column,
    normalize_action,
)
from ..exceptions import ValidationError
from ..mappers.value_converter import FALSY_STRINGS
from ..utils.file_values import FileValueResolver
from .base import Record, is_blank

if TYPE_CHECKING:
    from ..wordpress.site_schema import SiteSchema

# Поля WordPress (контент и термины), которые понимает маппинг
KNOWN_FIELDS = frozenset(SYSTEM_COLUMNS) | {
    # контент
    "title", "content", "excerpt", "slug", "status", "parent", "menu_order",
    "author", "template", "comment_status", "ping_status", "sticky", "format",
    "password", "date_gmt", "modified_gmt", "custom_permalink",
    # термины
    "name", "description", "count",
    # медиа
    "alt_text", "caption",
    # legacy SEO (Yoast-стиль, без точки)
    "seo_title", "seo_description", "seo_meta_keywords", "seo_focus_keyword",
    "seo_noindex", "seo_nofollow",
}

# SEO-поля по умолчанию; WP_SEO_FIELDS проекта добавляется к ним
DEFAULT_SEO_FIELDS = frozenset({
    "seo.title", "seo.description", "seo.keywords", "seo.h1", "seo.intent",
    "seo.breadcrumb", "seo.canonical",
    "seo.og.title", "seo.og.description", "seo.og.image",
}) | BOOL_FIELDS_DEFAULT_TRUE

# Поля, которые проверкой имён не трогаются: их набор задаёт сайт
FREE_PREFIXES = ("acf.", ACF_PREFIX, "meta.")

# Действия, при которых значения записи уходят в WordPress
SENDING_ACTIONS = {"add", "update"}
# Действия, которым нужен id существующей записи
ID_ACTIONS = {"update", "delete"}

VALID_STATUSES = {"publish", "draft", "pending", "private", "future"}

# Каноническое имя таксономии: строчные буквы/цифры, части через `_`/`-`
_CANONICAL_TAXONOMY = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
# `;` без пробела после — термины склеятся в один
_BAD_TERM_DELIMITER = re.compile(r";(?! )")

_BOOL_STRINGS = FALSY_STRINGS | {"1", "да", "yes", "true", "t", "y"}

# Сколько ошибок перечислять в сообщении (остальные — числом)
MAX_LISTED_ERRORS = 20


@dataclass
class FieldRules:
    """
    Что считается известным: таксономии, SEO-поля и свои поля проекта.

    Attributes:
        known_taxonomies: WP_TAXONOMIES — ручные исключения.
        site: Схема сайта (таксономии, типы, привязка). None — проверка без
            сайта: таксономии узнаются по соглашению об именах, WP_TAXONOMIES
            и по самому файлу (записи-термины с `taxonomy`).
        declared: Таксономии, объявленные в файле записями-терминами.
    """
    known_taxonomies: Optional[List[str]] = None
    seo_fields: Iterable[str] = ()
    extra_fields: Iterable[str] = ()
    site: Optional["SiteSchema"] = None
    declared: frozenset = frozenset()

    @classmethod
    def from_settings(cls, settings: Any = None, site: Optional["SiteSchema"] = None) -> "FieldRules":
        """Правила из .env проекта: WP_TAXONOMIES, WP_SEO_FIELDS, WP_EXTRA_FIELDS."""
        if settings is None:
            from ..config import Settings
            settings = Settings()
        return cls(
            known_taxonomies=settings.get_taxonomies(),
            seo_fields=settings.get_seo_fields() or (),
            extra_fields=settings.get_extra_fields(),
            site=site,
        )

    def with_declared(self, names: Iterable[str]) -> "FieldRules":
        return replace(self, declared=self.declared | frozenset(names))

    def taxonomies(self) -> Optional[List[str]]:
        """Все известные имена таксономий: .env, сайт, файл (None — нет ни одного)."""
        names = set(self.known_taxonomies or ()) | set(self.declared)
        if self.site is not None:
            names |= self.site.taxonomy_names()
        return sorted(names) or None

    def known_names(self) -> Set[str]:
        return set(KNOWN_FIELDS) | set(DEFAULT_SEO_FIELDS) | set(self.seo_fields) | set(self.extra_fields)


@dataclass
class TabCheck:
    """Итог проверки одного набора данных (лист Excel / таб JSON)."""
    tab: str
    records: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"tab": self.tab, "records": self.records, "errors": self.errors, "warnings": self.warnings}


def _default_where(name: str) -> Callable[[Record], str]:
    return lambda record: f"'{name}', запись {record.write_key}"


def check_records(
    name: str,
    records: Sequence[Record],
    rules: FieldRules,
    base_dir: Optional[Path] = None,
    where: Optional[Callable[[Record], str]] = None,
) -> TabCheck:
    """
    Проверить записи одного набора данных; исключений не бросает.

    Args:
        name: Имя листа / таба (для сообщений).
        records: Записи набора (уже после --filter, если он задан).
        rules: Что считается известным полем/таксономией.
        base_dir: Папка файла данных — от неё считаются значения-пути.
            None — ссылки на файлы не проверяются.
        where: Привязка записи к месту в файле для сообщений
            (по умолчанию — ключ обратной записи).
    """
    where = where or _default_where(name)
    result = TabCheck(tab=name, records=len(records))
    rules = rules.with_declared(r.discriminator for r in records if r.is_taxonomy)
    taxonomies = rules.taxonomies()
    # Сверка с сайтом: сообщение → (места, блокирует ли отправку)
    site_problems: Dict[str, Tuple[List[str], bool]] = {}
    known = rules.known_names()
    resolver = FileValueResolver(base_dir) if base_dir else None

    # Имя поля → места, где оно встретилось (отдельно у отправляемых записей)
    sending_fields: Dict[str, List[str]] = {}
    other_fields: Dict[str, List[str]] = {}
    # (тип, slug) → места
    slugs: Dict[tuple, List[str]] = {}

    for record in records:
        place = where(record)
        raw_action = record.data.get("action")
        action = normalize_action(raw_action)
        if action and action not in VALID_ACTIONS:
            result.warnings.append(f"{place}: неизвестный action {raw_action!r} — sync пропустит запись")

        sending = action in SENDING_ACTIONS
        acting = sending or action == "delete"
        bucket = sending_fields if sending else other_fields
        for name_ in record.data:
            bucket.setdefault(name_, []).append(place)

        if record.is_taxonomy and acting:
            problem = _taxonomy_name_problem(record.discriminator)
            if problem:
                result.errors.append(f"{place}: таксономия записи {problem}")

        if rules.site is not None:
            for problem, blocking in _site_problems(record, rules.site, taxonomies, sending, acting):
                places, was_blocking = site_problems.get(problem, ([], False))
                places.append(place)
                site_problems[problem] = (places, was_blocking or blocking)

        if acting:
            result.errors.extend(f"{place}: {p}" for p in _value_problems(record, action, rules))
            result.warnings.extend(f"{place}: {w}" for w in _value_warnings(record, action, rules))
            slug = record.data.get("slug")
            if sending and not is_blank(slug):
                key = (record.discriminator, record.is_taxonomy, str(slug).strip())
                slugs.setdefault(key, []).append(place)

        if resolver:
            for name_, value in record.data.items():
                problem = resolver.check(name_, value)
                if not problem:
                    continue
                if sending:
                    result.errors.append(f"{place}: {problem}")
                else:
                    result.warnings.append(
                        f"{place}: {problem} (action={action or 'пусто'} — на сайт не уйдёт)"
                    )

    for (discriminator, _, slug), places in slugs.items():
        if len(places) > 1:
            result.errors.append(
                f"'{name}': slug {slug!r} у {len(places)} записей типа '{discriminator}' "
                f"({', '.join(places)}) — вторая перезапишет первую"
            )

    for problem, (places, blocking) in site_problems.items():
        if blocking:
            result.errors.append(f"{places[0]}: {problem}{_more(places)}")
        else:
            result.warnings.append(f"{places[0]}: {problem}{_more(places)} (на сайт не уйдёт)")

    for name_, places in sending_fields.items():
        problem = _field_name_problem(name_, known, rules)
        if problem:
            result.errors.append(f"{places[0]}: {problem}{_more(places)}")
    for name_, places in other_fields.items():
        if name_ in sending_fields:
            continue
        problem = _field_name_problem(name_, known, rules)
        if problem:
            result.warnings.append(f"{places[0]}: {problem}{_more(places)} (на сайт не уйдёт)")

    return result


def _site_problems(
    record: Record,
    site: "SiteSchema",
    taxonomies: Optional[List[str]],
    sending: bool,
    acting: bool,
) -> List[Tuple[str, bool]]:
    """Расхождения записи с сайтом: (сообщение, блокирует ли отправку)."""
    problems: List[Tuple[str, bool]] = []
    discriminator = record.discriminator

    if record.is_taxonomy:
        if not site.taxonomy(discriminator):
            problems.append((
                f"таксономии '{discriminator}' на сайте нет{_hint(site.closest_taxonomy(discriminator))}",
                acting,
            ))
        return problems

    type_known = site.has_type(discriminator)
    if not type_known:
        problems.append((
            f"типа записи '{discriminator}' на сайте нет{_hint(site.closest_type(discriminator))}",
            acting,
        ))

    for name, value in record.data.items():
        if name.startswith(FREE_PREFIXES) or not is_taxonomy_column(name, taxonomies):
            continue
        if not site.taxonomy(name):
            problems.append((
                f"колонка '{name}': таксономии '{name}' на сайте нет"
                f"{_hint(site.closest_taxonomy(name))}",
                sending,
            ))
        elif type_known and not is_blank(value) and not site.is_attached(discriminator, name):
            problems.append((
                f"таксономия '{name}' не привязана к типу '{discriminator}' — WordPress проигнорирует поле",
                sending,
            ))
    return problems


def _hint(suggestion: Optional[str]) -> str:
    return f" — может, '{suggestion}'?" if suggestion else ""


def _more(places: List[str]) -> str:
    return f" (всего {len(places)} записей)" if len(places) > 1 else ""


def _taxonomy_name_problem(name: str) -> Optional[str]:
    if _CANONICAL_TAXONOMY.match(name):
        return None
    suggestion = re.sub(r"[^a-z0-9_-]+", "_", name.strip().lower()).strip("_-")
    return f"'{name}' не по стандарту (строчные буквы, цифры, '_'/'-') — должно быть '{suggestion}'"


def _field_name_problem(name: str, known: Set[str], rules: FieldRules) -> Optional[str]:
    """Проблема с именем поля (None — имя в порядке)."""
    if name.startswith(FREE_PREFIXES) or name in known:
        return None
    if is_taxonomy_column(name, rules.taxonomies()):
        problem = _taxonomy_name_problem(name)
        return f"колонка таксономии {problem}" if problem else None

    message = f"неизвестное поле '{name}'"
    if name.lower() in known:
        return f"{message} — может, '{name.lower()}'?"
    close = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.8)
    if close:
        return f"{message} — может, '{close[0]}'?"
    return f"{message} (опечатка? своё поле — добавьте в WP_EXTRA_FIELDS)"


def _value_problems(record: Record, action: str, rules: FieldRules) -> List[str]:
    """Проблемы значений записи, которую sync отправит или удалит."""
    data = record.data
    problems: List[str] = []

    if action in ID_ACTIONS and is_blank(data.get("id")):
        problems.append(f"action={action}, но id не заполнен")
    if action == "delete":
        return problems

    if not record.is_taxonomy:
        status = data.get("status")
        if not is_blank(status) and str(status).strip() not in VALID_STATUSES:
            problems.append(
                f"status {status!r} не из допустимых: {', '.join(sorted(VALID_STATUSES))}"
            )

    for name, value in data.items():
        if is_blank(value):
            continue
        if name in BOOL_FIELDS_DEFAULT_TRUE and not _is_bool_value(value):
            problems.append(f"'{name}' = {value!r}: ожидается да/нет, 1/0, true/false")
        elif (
            isinstance(value, str)
            and not name.startswith(FREE_PREFIXES)
            and is_taxonomy_column(name, rules.taxonomies())
            and _BAD_TERM_DELIMITER.search(value)
        ):
            problems.append(
                f"'{name}' = {value!r}: термины разделяются '{TAXONOMY_DELIMITER}' "
                "(точка с запятой и пробел)"
            )

    return problems


def _is_bool_value(value: Any) -> bool:
    if isinstance(value, (bool, int, float)):
        return True
    return str(value).strip().lower() in _BOOL_STRINGS


def _value_warnings(record: Record, action: str, rules: FieldRules) -> List[str]:
    """Подозрительное, но не блокирующее: id у add, запятая в терминах."""
    warnings: List[str] = []
    if action == "add" and not is_blank(record.data.get("id")):
        warnings.append("action=add, но id заполнен — будет создана новая запись (может, нужен update?)")
    if action in SENDING_ACTIONS:
        for name, value in record.data.items():
            if (
                isinstance(value, str)
                and not name.startswith(FREE_PREFIXES)
                and is_taxonomy_column(name, rules.taxonomies())
                and "," in value
            ):
                warnings.append(
                    f"'{name}' = {value!r}: запятая не разделяет термины — "
                    f"это один термин (разделитель '{TAXONOMY_DELIMITER}')"
                )
    return warnings


def check_sources(
    sources: Iterable[Any],
    rules: FieldRules,
    base_dir: Optional[Path] = None,
    keep: Optional[Callable[[Record], bool]] = None,
) -> List[TabCheck]:
    """
    Проверить несколько источников (ExcelRecordSource / JsonRecordSource).

    Args:
        sources: Источники с `read()`, `where(record)` и именем
            (`sheet_name` у Excel, `tab_name` у JSON).
        keep: Отбор записей, которые реально пойдут в прогон (--filter,
            --skip-tax): отброшенные не проверяются.
    """
    read = [(source, list(source.read())) for source in sources]
    # Таксономии, объявленные файлом (записи-термины, листы таксономий Excel),
    # действуют на все наборы: лист `rb_topic` делает таксономией колонку
    # `rb_topic` на листе `rb_services`.
    rules = rules.with_declared(
        r.discriminator for _, records in read for r in records if r.is_taxonomy
    )
    checks = []
    for source, records in read:
        name = getattr(source, "sheet_name", None) or getattr(source, "tab_name")
        kept = [r for r in records if keep is None or keep(r)]
        checks.append(check_records(name, kept, rules, base_dir=base_dir, where=source.where))
    return checks


def raise_if_errors(file_name: str, checks: Sequence[TabCheck]) -> None:
    """Все ошибки наборов одним сообщением (exit 2)."""
    errors = [error for check in checks for error in check.errors]
    if not errors:
        return
    listed = "\n".join(f"  - {e}" for e in errors[:MAX_LISTED_ERRORS])
    more = len(errors) - MAX_LISTED_ERRORS
    tail = f"\n  … и ещё {more}" if more > 0 else ""
    raise ValidationError(f"{file_name}: ошибок — {len(errors)}:\n{listed}{tail}")
