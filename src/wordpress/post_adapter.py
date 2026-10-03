"""
WP-адаптер для Windmill (T104): одна запись за вызов — `post upsert`,
`post get`, `media attach`.

Разбор входа (`parse_upsert_input`, `FeaturedSource`) не ходит в сеть — ошибки
входа отсекаются до доступов. Операции с сайтом (`upsert`, `get_post`,
`attach_featured`) принимают открытый `WordPressClient`.

Правила (решения dev, ~/Work/tools/docs/t104-wp-adapter.md):
- `id` → обновление; без id — поиск по тип+slug среди всех статусов: один
  черновик → обновление, не-черновик (или несколько кандидатов) → `Conflict`,
  ничего не найдено → создание;
- создаётся только `draft`, статус существующей записи не трогается;
- content — готовый HTML/Gutenberg, без конвертации;
- content_hash — sha256 от `content.raw` ответа WordPress (context=edit);
- SEO — только REST-поле `seo` плагина rb-seo; нет поля у типа — `SeoNotSupported`
  до любой записи (иначе WordPress молча проигнорировал бы его).
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..exceptions import Conflict, NotFoundError, RecordNotFound, SeoNotSupported, ValidationError
from .client import WordPressClient
from .media import MediaEndpoint

API = "/wp-json/wp/v2"

# Статусы, среди которых ищется запись по slug: все, кроме корзины и автосохранений.
LOOKUP_STATUSES = ("publish", "future", "draft", "pending", "private")
DRAFT = "draft"

UPSERT_FIELDS = ("type", "id", "slug", "title", "content", "seo", "featured")
FEATURED_SOURCES = ("path", "url", "id")
FEATURED_FIELDS = (*FEATURED_SOURCES, "alt")


def content_hash(raw: str) -> str:
    """sha256 сырого контента (полный hex): одинаковый текст — одинаковый хэш."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Вход ──────────────────────────────────────────────────────────────────


@dataclass
class FeaturedSource:
    """Источник изображения записи: ровно одно из path / url / id, плюс alt."""

    path: Optional[Path] = None
    url: Optional[str] = None
    media_id: Optional[int] = None
    alt: Optional[str] = None

    @classmethod
    def build(
        cls,
        path: Optional[str] = None,
        url: Optional[str] = None,
        media_id: Optional[int] = None,
        alt: Optional[str] = None,
        where: str = "featured",
    ) -> "FeaturedSource":
        given = [name for name, value in (("path", path), ("url", url), ("id", media_id)) if value is not None]
        if len(given) != 1:
            raise ValidationError(
                f"{where}: нужен ровно один источник изображения — path, url или id "
                f"(передано: {', '.join(given) or 'ничего'})"
            )
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise ValidationError(f"{where}: path должен быть непустой строкой")
        if url is not None and (not isinstance(url, str) or not url.startswith(("http://", "https://"))):
            raise ValidationError(f"{where}: url должен начинаться с http:// или https://")
        if media_id is not None and (not isinstance(media_id, int) or isinstance(media_id, bool) or media_id <= 0):
            raise ValidationError(f"{where}: id должен быть положительным целым")
        if alt is not None and not isinstance(alt, str):
            raise ValidationError(f"{where}: alt должен быть строкой")
        return cls(path=Path(path) if path is not None else None, url=url, media_id=media_id, alt=alt)

    def check_file(self) -> None:
        """Локальный файл обязан быть на диске до любой записи на сайт."""
        if self.path is not None and not self.path.is_file():
            raise RecordNotFound(f"Файл изображения не найден: {self.path}")


@dataclass
class UpsertInput:
    """Разобранный вход `post upsert`."""

    post_type: str = "post"
    post_id: Optional[int] = None
    slug: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)  # title / content / slug / seo — как пришли
    featured: Optional[FeaturedSource] = None


def parse_upsert_input(text: str, id_flag: Optional[int] = None) -> UpsertInput:
    """
    Разобрать и проверить JSON-вход `post upsert` целиком, без сети.

    Все ошибки — `ValidationError` (exit 2): невалидный JSON, не объект,
    неизвестные поля (опечатка `contnet` иначе молча потерялась бы), типы,
    нет ни id, ни slug, нет title там, где запись может быть создана.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Вход не читается как JSON: {e}")
    if not isinstance(data, dict):
        raise ValidationError("Вход должен быть JSON-объектом {slug, title, content, …}")

    unknown = sorted(set(data) - set(UPSERT_FIELDS))
    if unknown:
        raise ValidationError(
            f"Неизвестные поля входа: {', '.join(unknown)}",
            action=f"Допустимы: {', '.join(UPSERT_FIELDS)}. Статус не задаётся: создаётся только черновик.",
        )

    post_type = data.get("type", "post")
    if not isinstance(post_type, str) or not post_type.strip():
        raise ValidationError("type должен быть непустой строкой (имя типа записи WordPress, например post)")

    post_id = data.get("id")
    if post_id is not None and (not isinstance(post_id, int) or isinstance(post_id, bool) or post_id <= 0):
        raise ValidationError("id должен быть положительным целым")
    if id_flag is not None:
        if post_id is not None and post_id != id_flag:
            raise ValidationError(f"--id {id_flag} не совпадает с id {post_id} во входе")
        post_id = id_flag

    fields: Dict[str, Any] = {}
    for name in ("slug", "title", "content"):
        if name in data:
            if not isinstance(data[name], str):
                raise ValidationError(f"{name} должен быть строкой")
            fields[name] = data[name]
    if "slug" in fields and not fields["slug"].strip():
        raise ValidationError("slug не может быть пустым")
    if "seo" in data:
        if not isinstance(data["seo"], dict) or not data["seo"]:
            raise ValidationError("seo должен быть непустым объектом {title, description, …}")
        fields["seo"] = data["seo"]

    if post_id is None:
        if "slug" not in fields:
            raise ValidationError("Нужен id (обновление) или slug (поиск по slug, иначе создание)")
        if not fields.get("title", "").strip():
            raise ValidationError("Нужен непустой title: без id запись может быть создана")

    featured = None
    if "featured" in data:
        raw = data["featured"]
        if not isinstance(raw, dict):
            raise ValidationError("featured должен быть объектом {path|url|id, alt?}")
        unknown = sorted(set(raw) - set(FEATURED_FIELDS))
        if unknown:
            raise ValidationError(f"featured: неизвестные поля {', '.join(unknown)}")
        featured = FeaturedSource.build(raw.get("path"), raw.get("url"), raw.get("id"), raw.get("alt"))

    return UpsertInput(
        post_type=post_type.strip(),
        post_id=post_id,
        slug=fields.get("slug"),
        fields=fields,
        featured=featured,
    )


# ── Сайт ──────────────────────────────────────────────────────────────────


async def rest_base(client: WordPressClient, post_type: str) -> str:
    """REST-база типа записи (`post` → `posts`); неизвестный тип — ошибка входа."""
    try:
        info = await client.get(f"{API}/types/{post_type}")
    except NotFoundError:
        raise ValidationError(f"Тип записи '{post_type}' на сайте не найден (или не доступен в REST)")
    return info.get("rest_base") or post_type


async def require_seo_field(client: WordPressClient, post_type: str, base: str) -> None:
    """Поле `seo` есть в схеме REST типа (rb-seo включён для него), иначе `SeoNotSupported`."""
    schema = await client.options(f"{API}/{base}")
    properties = (schema.get("schema") or {}).get("properties") or {}
    if "seo" not in properties:
        raise SeoNotSupported(
            f"У типа '{post_type}' нет REST-поля seo: rb-seo для него выключен — SEO не сохранилось бы",
            action="Включите тип в настройках rb-seo или уберите seo из входа.",
        )


async def _get_item(client: WordPressClient, base: str, post_id: int, what: str = "Запись") -> Dict[str, Any]:
    try:
        return await client.get(f"{API}/{base}/{post_id}", {"context": "edit"})
    except NotFoundError:
        raise RecordNotFound(f"{what} id={post_id} не найдена")


async def _find_by_slug(client: WordPressClient, base: str, slug: str) -> List[Dict[str, Any]]:
    found = await client.get(
        f"{API}/{base}",
        {"slug": slug, "status": ",".join(LOOKUP_STATUSES), "context": "edit", "per_page": 100},
    )
    return found if isinstance(found, list) else [found]


def _choose_target(post_type: str, slug: str, found: List[Dict[str, Any]]) -> Optional[int]:
    """Один черновик — его id; ничего — None (создать); иначе `Conflict`."""
    if not found:
        return None
    live = [item for item in found if item.get("status") != DRAFT]
    if live:
        item = live[0]
        raise Conflict(
            f"{post_type} со slug '{slug}' уже есть: id={item['id']}, статус {item.get('status')} — "
            "не черновик, не изменён",
            post_id=item["id"],
            action="Опубликованное обновляется только явным --id; иначе выберите другой slug.",
        )
    if len(found) > 1:
        ids = ", ".join(str(item["id"]) for item in found)
        raise Conflict(
            f"{post_type} со slug '{slug}': несколько черновиков (id {ids}) — неясно, какой обновлять",
            post_id=found[0]["id"],
            action="Передайте нужный id явно.",
        )
    return found[0]["id"]


async def resolve_featured(client: WordPressClient, featured: FeaturedSource) -> Dict[str, Any]:
    """Медиа для изображения записи: существующее по id или загруженное (path/url). → {id, source_url}."""
    media = MediaEndpoint(client)
    if featured.media_id is not None:
        try:
            item = await media.get(featured.media_id)
        except NotFoundError:
            raise RecordNotFound(f"Медиа id={featured.media_id} не найдено")
        if featured.alt is not None:
            await media.update(featured.media_id, alt_text=featured.alt)
    elif featured.path is not None:
        item = await media.upload_file(featured.path, alt_text=featured.alt)
    else:
        item = await media.upload_from_url(str(featured.url), alt_text=featured.alt)
    return {"id": item["id"], "source_url": item.get("source_url")}


async def _with_raw_content(client: WordPressClient, base: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """Ответ с `content.raw`; если сайт его не отдал — перечитать запись в context=edit."""
    content = item.get("content")
    if isinstance(content, dict) and isinstance(content.get("raw"), str):
        return item
    return await _get_item(client, base, item["id"])


def _raw(item: Dict[str, Any], name: str) -> Any:
    value = item.get(name)
    return value.get("raw", value.get("rendered")) if isinstance(value, dict) else value


async def upsert(client: WordPressClient, spec: UpsertInput) -> Dict[str, Any]:
    """
    Создать черновик или обновить запись. Порядок: тип → seo-поле → цель
    (id / slug) → медиа → запись. Всё, что может отказать без изменений
    (нет типа, нет seo, нет записи, конфликт), проверяется до первой записи.
    """
    base = await rest_base(client, spec.post_type)
    if "seo" in spec.fields:
        await require_seo_field(client, spec.post_type, base)

    target_id = spec.post_id
    if target_id is not None:
        await _get_item(client, base, target_id)
    else:
        target_id = _choose_target(spec.post_type, str(spec.slug), await _find_by_slug(client, base, str(spec.slug)))

    payload = dict(spec.fields)
    if spec.featured is not None:
        media = await resolve_featured(client, spec.featured)
        payload["featured_media"] = media["id"]

    created = target_id is None
    if created:
        item = await client.post(f"{API}/{base}", {**payload, "status": DRAFT})
    elif payload:
        item = await client.put(f"{API}/{base}/{target_id}", payload)
    else:
        item = await _get_item(client, base, int(target_id))
    item = await _with_raw_content(client, base, item)

    result = {
        "id": item["id"],
        "url": item.get("link"),
        "status": item.get("status"),
        "modified": item.get("modified"),
        "content_hash": content_hash(_raw(item, "content") or ""),
        "created": created,
    }
    if spec.featured is not None:
        result["featured_media"] = item.get("featured_media")
    return result


async def get_post(client: WordPressClient, post_type: str, post_id: int) -> Dict[str, Any]:
    """Запись в context=edit: сырые title/content, seo (None — у типа нет поля), хэш как у upsert."""
    base = await rest_base(client, post_type)
    item = await _get_item(client, base, post_id)
    content = _raw(item, "content") or ""
    return {
        "id": item["id"],
        "type": item.get("type", post_type),
        "slug": item.get("slug"),
        "title": _raw(item, "title"),
        "content": content,
        "status": item.get("status"),
        "url": item.get("link"),
        "modified": item.get("modified"),
        "seo": item.get("seo"),
        "featured_media": item.get("featured_media"),
        "content_hash": content_hash(content),
    }


async def attach_featured(
    client: WordPressClient, post_type: str, post_id: int, featured: FeaturedSource
) -> Dict[str, Any]:
    """Сделать медиа изображением записи. Запись проверяется до загрузки файла."""
    base = await rest_base(client, post_type)
    await _get_item(client, base, post_id)
    media = await resolve_featured(client, featured)
    await client.put(f"{API}/{base}/{post_id}", {"featured_media": media["id"]})
    return {"media_id": media["id"], "source_url": media["source_url"], "post_id": post_id}
