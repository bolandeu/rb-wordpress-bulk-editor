"""
Схема сайта: какие таксономии и типы записей есть и что к чему привязано.

Источник правды — сам сайт: список берётся из REST API (`/wp/v2/taxonomies`,
`/wp/v2/types`) при каждом прогоне, поэтому новые таксономии подхватываются
без правок `.env` (`WP_TAXONOMIES` остаётся только для ручных исключений).

Имена принимаются в обеих формах, как они встречаются в файлах данных:
slug (`category`, `post`) и REST-имя (`categories`, `posts`).

Ошибка загрузки пробрасывается, а не заменяется набором по умолчанию: иначе
проверка перед отправкой сочла бы `rb_topic` обычным полем, а sync отправил бы
его мимо таксономии.
"""

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Set

TAXONOMIES_ENDPOINT = "/wp-json/wp/v2/taxonomies"
TYPES_ENDPOINT = "/wp-json/wp/v2/types"


@dataclass
class SiteSchema:
    """
    Attributes:
        taxonomies: Любое имя таксономии (slug / REST-имя) → slug.
        types: Любое имя типа записи (slug / REST-имя) → slug.
        attached: slug типа → slug'и привязанных к нему таксономий.
    """

    taxonomies: Dict[str, str] = field(default_factory=dict)
    types: Dict[str, str] = field(default_factory=dict)
    attached: Dict[str, Set[str]] = field(default_factory=dict)

    @classmethod
    def from_rest(cls, taxonomies: Mapping[str, Any], types: Mapping[str, Any]) -> "SiteSchema":
        schema = cls()
        for key, info in (types or {}).items():
            slug = info.get("slug") or key
            for name in (key, slug, info.get("rest_base")):
                if name:
                    schema.types[name] = slug
        for key, info in (taxonomies or {}).items():
            slug = info.get("slug") or key
            for name in (key, slug, info.get("rest_base")):
                if name:
                    schema.taxonomies[name] = slug
            for type_slug in info.get("types") or []:
                schema.attached.setdefault(type_slug, set()).add(slug)
        return schema

    def taxonomy(self, name: str) -> Optional[str]:
        """slug таксономии по любому её имени (None — на сайте такой нет)."""
        return self.taxonomies.get(name)

    def has_type(self, name: str) -> bool:
        return name in self.types

    def is_attached(self, type_name: str, taxonomy_name: str) -> bool:
        """Привязана ли таксономия к типу записи (иначе WordPress проигнорирует поле)."""
        type_slug = self.types.get(type_name)
        taxonomy_slug = self.taxonomies.get(taxonomy_name)
        return bool(type_slug and taxonomy_slug) and taxonomy_slug in self.attached.get(type_slug, set())

    def taxonomy_names(self) -> Set[str]:
        """Все имена таксономий сайта — для распознавания колонок."""
        return set(self.taxonomies)

    def closest_taxonomy(self, name: str) -> Optional[str]:
        return _closest(name, self.taxonomies)

    def closest_type(self, name: str) -> Optional[str]:
        return _closest(name, self.types)


def _closest(name: str, names: Mapping[str, str]) -> Optional[str]:
    match = difflib.get_close_matches(name, sorted(names), n=1, cutoff=0.8)
    return match[0] if match else None


async def load_site_schema(client: Any) -> SiteSchema:
    """Два запроса на чтение. Ошибки (сеть, доступ) пробрасываются как есть."""
    taxonomies = await client.get(TAXONOMIES_ENDPOINT)
    types = await client.get(TYPES_ENDPOINT)
    return SiteSchema.from_rest(taxonomies, types)
