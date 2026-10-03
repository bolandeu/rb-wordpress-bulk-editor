"""
Фальшивый WordPress для тестов команд `post` / `media attach`: httpx-обработчик
со своим состоянием (записи, медиа, типы), повторяющий те места REST API, на
которые опираются команды:

- `GET /wp/v2/types/<type>` — rest_base типа (404 — нет такого типа);
- `OPTIONS /wp/v2/<rest_base>` — схема коллекции; поле `seo` есть только у
  типов, где rb-seo включён (`seo_types`);
- `GET /wp/v2/<rest_base>?slug=…&status=…` — поиск по slug среди статусов;
- `POST /wp/v2/<rest_base>` — создание, `POST|PUT /wp/v2/<rest_base>/<id>` —
  обновление (404 — нет записи); ответы, как у WordPress, в context=edit;
- `GET /wp/v2/<rest_base>/<id>` — запись;
- `POST /wp/v2/media` — загрузка (бинарь), `POST /wp/v2/media/<id>` — alt,
  `GET /wp/v2/media/<id>` — медиа (404 — нет).

Неверный пароль — 401 на любой запрос. Поле `seo` у типа без rb-seo WordPress
молча игнорирует — так и здесь.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import httpx

USER = "editor"
TOKEN = "app-pass-0000"
SITE = "example.com"
ENV_USER = "WP_API_USER"
ENV_TOKEN = "WP_API_TOKEN"

TYPES = {"post": "posts", "page": "pages"}


class FakeWordPress:
    def __init__(self, seo_types: Optional[set] = None):
        self.posts: Dict[int, Dict[str, Any]] = {}
        self.media: Dict[int, Dict[str, Any]] = {}
        self.seo_types = {"posts"} if seo_types is None else seo_types
        self.requests: List[httpx.Request] = []
        self.writes: List[httpx.Request] = []
        self.password = TOKEN
        self._next_id = 100
        self._clock = 0

    # ── Наполнение ────────────────────────────────────────────────────

    def add_post(self, slug: str, status: str = "draft", rest_base: str = "posts", **fields: Any) -> int:
        post_id = self._new_id()
        self.posts[post_id] = {
            "id": post_id,
            "rest_base": rest_base,
            "slug": slug,
            "status": status,
            "title": fields.get("title", f"Запись {slug}"),
            "content": fields.get("content", "<p>старый текст</p>"),
            "seo": fields.get("seo", {"title": "", "description": ""}),
            "featured_media": fields.get("featured_media", 0),
            "modified": self._tick(),
        }
        return post_id

    def add_media(self, name: str = "old.jpg") -> int:
        media_id = self._new_id()
        self.media[media_id] = {"id": media_id, "source_url": f"https://{SITE}/up/{name}", "alt_text": ""}
        return media_id

    def posts_of(self, rest_base: str = "posts") -> List[Dict[str, Any]]:
        return [p for p in self.posts.values() if p["rest_base"] == rest_base]

    # ── Внутреннее ────────────────────────────────────────────────────

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _tick(self) -> str:
        self._clock += 1
        return f"2026-10-03T10:00:{self._clock:02d}"

    def _item(self, post: Dict[str, Any]) -> Dict[str, Any]:
        item = {
            "id": post["id"],
            "type": next(t for t, base in TYPES.items() if base == post["rest_base"]),
            "slug": post["slug"],
            "status": post["status"],
            "link": f"https://{SITE}/?p={post['id']}",
            "modified": post["modified"],
            "modified_gmt": post["modified"],
            "title": {"raw": post["title"], "rendered": post["title"]},
            # rendered ≠ raw, как у настоящего WP (wpautop/фильтры): хэш — только по raw
            "content": {"raw": post["content"], "rendered": post["content"] + "\n", "protected": False},
            "featured_media": post["featured_media"],
        }
        if post["rest_base"] in self.seo_types:
            item["seo"] = dict(post["seo"])
        return item

    def _authorized(self, request: httpx.Request) -> bool:
        expected = base64.b64encode(f"{USER}:{self.password}".encode()).decode()
        return request.headers.get("Authorization") == f"Basic {expected}"

    def _apply(self, post: Dict[str, Any], data: Dict[str, Any]) -> None:
        for field in ("slug", "status", "title", "content", "featured_media"):
            if field in data:
                post[field] = data[field]
        if "seo" in data and post["rest_base"] in self.seo_types:
            post["seo"] = {**post["seo"], **data["seo"]}
        post["modified"] = self._tick()

    # ── Обработчик ────────────────────────────────────────────────────

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method in ("POST", "PUT", "DELETE"):
            self.writes.append(request)
        if not self._authorized(request):
            return httpx.Response(401, json={"code": "rest_not_logged_in", "message": "Неверный пароль"})

        path = request.url.path.removeprefix("/wp-json/wp/v2/")
        parts = path.strip("/").split("/")
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}

        if parts[0] == "types":
            if len(parts) == 2 and parts[1] in TYPES:
                return httpx.Response(200, json={"slug": parts[1], "rest_base": TYPES[parts[1]]})
            return httpx.Response(404, json={"code": "rest_type_invalid", "message": "Invalid post type."})

        if parts[0] == "media":
            return self._media(request, parts)

        rest_base = parts[0]
        if rest_base not in TYPES.values():
            return httpx.Response(404, json={"code": "rest_no_route", "message": "No route"})

        if request.method == "OPTIONS":
            properties = {"id": {}, "title": {}, "content": {}, "slug": {}, "status": {}}
            if rest_base in self.seo_types:
                properties["seo"] = {"type": "object"}
            return httpx.Response(200, json={"schema": {"properties": properties}})

        if len(parts) == 1 and request.method == "GET":
            statuses = set(query.get("status", "publish").split(","))
            found = [
                self._item(p)
                for p in self.posts_of(rest_base)
                if p["slug"] == query.get("slug") and p["status"] in statuses
            ]
            return httpx.Response(200, json=found)

        if len(parts) == 1 and request.method == "POST":
            data = json.loads(request.content)
            post_id = self._new_id()
            post = {
                "id": post_id,
                "rest_base": rest_base,
                "slug": "",
                "status": "draft",
                "title": "",
                "content": "",
                "seo": {"title": "", "description": ""},
                "featured_media": 0,
            }
            self._apply(post, data)
            self.posts[post_id] = post
            return httpx.Response(201, json=self._item(post))

        post_id = int(parts[1])
        post = self.posts.get(post_id)
        if post is None or post["rest_base"] != rest_base:
            return httpx.Response(404, json={"code": "rest_post_invalid_id", "message": "Invalid post ID."})
        if request.method == "GET":
            return httpx.Response(200, json=self._item(post))
        if request.method in ("POST", "PUT"):
            self._apply(post, json.loads(request.content))
            return httpx.Response(200, json=self._item(post))
        return httpx.Response(405)

    def _media(self, request: httpx.Request, parts: List[str]) -> httpx.Response:
        if len(parts) == 1 and request.method == "POST":
            name = request.headers["Content-Disposition"].split('filename="')[1].rstrip('"')
            media_id = self.add_media(name)
            return httpx.Response(201, json=self.media[media_id])
        media_id = int(parts[1])
        media = self.media.get(media_id)
        if media is None:
            return httpx.Response(404, json={"code": "rest_post_invalid_id", "message": "Invalid post ID."})
        if request.method == "POST":
            media.update(json.loads(request.content))
        return httpx.Response(200, json=media)
