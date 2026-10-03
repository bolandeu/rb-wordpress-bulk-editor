"""
Tests for команд `post upsert` / `post get` / `media attach` — WP-адаптер для
Windmill (T104, сценарии ~/Work/tools/docs/t104-wp-adapter.md).

Сайт подменён `tests/fake_wp.FakeWordPress` через httpx.MockTransport. Доступы —
только переменные окружения `WP_API_USER` / `WP_API_TOKEN`: ни keychain, ни
`.env` новые команды не читают.
"""

import hashlib
import json
from pathlib import Path

import httpx
import keyring
import pytest
from typer.testing import CliRunner

from src.cli import app
from src.wordpress.client import WordPressClient
from tests.fake_wp import ENV_TOKEN, ENV_USER, SITE, TOKEN, USER, FakeWordPress

runner = CliRunner()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def wp(monkeypatch, tmp_path):
    """Фальшивый сайт + env-доступы; cwd — пустая временная папка (логи туда не пишутся)."""
    from src.cli import post_commands

    fake = FakeWordPress()
    for name in ("WP_DOMAIN", "SITE_DOMAIN", "WP_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(ENV_USER, USER)
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        post_commands,
        "WordPressClient",
        lambda config: WordPressClient(config, transport=httpx.MockTransport(fake.handler)),
    )

    def _no_keychain(*args, **kwargs):
        raise AssertionError("новые команды не должны обращаться к keychain")

    monkeypatch.setattr(keyring, "get_password", _no_keychain)
    return fake


def invoke(args, data=None):
    """Запустить команду с --json; data — тело для --input - (stdin)."""
    stdin = None
    if data is not None:
        stdin = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        args = [*args, "--input", "-"]
    result = runner.invoke(app, [*args, "--json"], input=stdin)
    lines = result.stdout.strip().splitlines()
    payload = json.loads(lines[-1]) if lines else None
    return result, payload


def upsert(data, *extra):
    return invoke(["post", "upsert", "--site", SITE, *extra], data)


def get_post(post_id, *extra):
    return invoke(["post", "get", "--id", str(post_id), "--site", SITE, *extra])


def attach(post_id, *extra):
    return invoke(["media", "attach", "--post", str(post_id), "--site", SITE, *extra])


class TestUpsertCreate:
    def test_creates_draft_when_slug_is_new(self, wp):
        result, payload = upsert({"slug": "x", "title": "Заголовок", "content": "<p>Текст</p>"})

        assert result.exit_code == 0, result.output
        assert payload["ok"] is True
        assert payload["created"] is True
        [post] = wp.posts_of("posts")
        assert post["status"] == "draft"
        assert payload["id"] == post["id"]
        assert payload["status"] == "draft"
        assert payload["url"].startswith(f"https://{SITE}/")
        assert payload["modified"]
        assert payload["content_hash"] == sha256("<p>Текст</p>")

    def test_status_in_input_is_rejected(self, wp):
        """upsert создаёт только черновики: статус задаёт человек, не адаптер."""
        result, payload = upsert({"slug": "x", "title": "T", "content": "c", "status": "publish"})

        assert result.exit_code == 2
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert wp.requests == []

    def test_input_file(self, wp, tmp_path):
        source = tmp_path / "post.json"
        source.write_text(json.dumps({"slug": "x", "title": "Т", "content": "c"}), encoding="utf-8")

        result, payload = invoke(["post", "upsert", "--site", SITE, "--input", str(source)])

        assert result.exit_code == 0, result.output
        assert payload["created"] is True

    def test_content_is_sent_as_is(self, wp):
        """Решение 2: готовый HTML/Gutenberg, без конвертации."""
        content = "<!-- wp:paragraph --><p>Блок</p><!-- /wp:paragraph -->\n<div class='x'>сырой</div>"

        result, payload = upsert({"slug": "x", "title": "T", "content": content})

        assert result.exit_code == 0, result.output
        [post] = wp.posts_of("posts")
        assert post["content"] == content
        assert payload["content_hash"] == sha256(content)

    def test_type_selects_rest_base(self, wp):
        wp.seo_types = {"posts", "pages"}

        result, payload = upsert({"type": "page", "slug": "about", "title": "О нас", "content": ""})

        assert result.exit_code == 0, result.output
        assert [p["slug"] for p in wp.posts_of("pages")] == ["about"]
        assert wp.posts_of("posts") == []


class TestUpsertUpdate:
    def test_update_by_id_in_input(self, wp):
        post_id = wp.add_post("x", content="<p>сохранённый</p>")

        result, payload = upsert({"id": post_id, "title": "новый"})

        assert result.exit_code == 0, result.output
        assert payload["created"] is False and payload["id"] == post_id
        assert wp.posts[post_id]["title"] == "новый"
        assert payload["content_hash"] == sha256("<p>сохранённый</p>")

    def test_update_by_id_flag(self, wp):
        post_id = wp.add_post("x")

        result, payload = upsert({"title": "новый"}, "--id", str(post_id))

        assert result.exit_code == 0, result.output
        assert payload["id"] == post_id and wp.posts[post_id]["title"] == "новый"

    def test_id_flag_and_input_must_agree(self, wp):
        post_id = wp.add_post("x")

        result, payload = upsert({"id": post_id + 1, "title": "новый"}, "--id", str(post_id))

        assert result.exit_code == 2
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert wp.requests == []

    def test_draft_with_same_slug_is_updated_not_duplicated(self, wp):
        post_id = wp.add_post("x", status="draft")

        result, payload = upsert({"slug": "x", "title": "обновлён", "content": "<p>новый</p>"})

        assert result.exit_code == 0, result.output
        assert payload["created"] is False and payload["id"] == post_id
        assert len(wp.posts_of("posts")) == 1
        assert wp.posts[post_id]["content"] == "<p>новый</p>"
        assert wp.posts[post_id]["status"] == "draft"

    @pytest.mark.parametrize("status", ["publish", "pending", "future", "private"])
    def test_non_draft_with_same_slug_is_conflict(self, wp, status):
        post_id = wp.add_post("x", status=status, content="<p>живой</p>")

        result, payload = upsert({"slug": "x", "title": "T", "content": "<p>подмена</p>"})

        assert result.exit_code == 1
        assert payload["ok"] is False
        assert payload["error"]["code"] == "CONFLICT"
        assert str(post_id) in payload["error"]["message"]
        assert payload["error"]["post_id"] == post_id
        assert wp.writes == []
        assert wp.posts[post_id]["content"] == "<p>живой</p>"
        assert len(wp.posts_of("posts")) == 1

    def test_published_updated_by_explicit_id_keeps_status(self, wp):
        post_id = wp.add_post("x", status="publish")

        result, payload = upsert({"id": post_id, "content": "<p>правка</p>"})

        assert result.exit_code == 0, result.output
        assert wp.posts[post_id]["content"] == "<p>правка</p>"
        assert wp.posts[post_id]["status"] == "publish"
        assert payload["status"] == "publish"
        sent = json.loads(wp.writes[-1].content)
        assert "status" not in sent

    def test_missing_id_is_not_found_and_nothing_created(self, wp):
        result, payload = upsert({"id": 9999, "title": "T"})

        assert result.exit_code == 1
        assert payload["error"]["code"] == "NOT_FOUND"
        assert wp.posts == {}

    def test_repeat_is_idempotent(self, wp):
        data = {"slug": "x", "title": "T", "content": "<p>один</p>"}

        first_result, first = upsert(data)
        second_result, second = upsert(data)

        assert first_result.exit_code == 0 and second_result.exit_code == 0
        assert first["content_hash"] == second["content_hash"]
        assert first["id"] == second["id"]
        assert first["created"] is True and second["created"] is False
        assert len(wp.posts_of("posts")) == 1


class TestUpsertSeo:
    def test_seo_round_trip(self, wp):
        seo = {"title": "SEO-заголовок", "description": "Описание"}

        result, payload = upsert({"slug": "x", "title": "T", "content": "c", "seo": seo})
        assert result.exit_code == 0, result.output

        got_result, got = get_post(payload["id"])
        assert got_result.exit_code == 0, got_result.output
        assert got["seo"]["title"] == "SEO-заголовок"
        assert got["seo"]["description"] == "Описание"

    def test_type_without_seo_field_is_refused_before_any_write(self, wp):
        wp.seo_types = set()

        result, payload = upsert({"slug": "x", "title": "T", "content": "c", "seo": {"title": "S"}})

        assert result.exit_code == 1
        assert payload["error"]["code"] == "SEO_NOT_SUPPORTED"
        assert "post" in payload["error"]["message"]
        assert wp.writes == [] and wp.posts == {}

    def test_seo_refused_for_existing_post_too(self, wp):
        wp.seo_types = set()
        post_id = wp.add_post("x")

        result, payload = upsert({"id": post_id, "seo": {"title": "S"}})

        assert result.exit_code == 1
        assert payload["error"]["code"] == "SEO_NOT_SUPPORTED"
        assert wp.writes == []

    def test_without_seo_no_schema_check_needed(self, wp):
        wp.seo_types = set()

        result, payload = upsert({"slug": "x", "title": "T", "content": "c"})

        assert result.exit_code == 0, result.output


class TestUpsertFeatured:
    def test_featured_path_uploaded_and_attached(self, wp, tmp_path):
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8fake")

        result, payload = upsert(
            {"slug": "x", "title": "T", "content": "c", "featured": {"path": str(image), "alt": "Обложка"}}
        )

        assert result.exit_code == 0, result.output
        [media_id] = list(wp.media)
        assert payload["featured_media"] == media_id
        [post] = wp.posts_of("posts")
        assert post["featured_media"] == media_id
        assert wp.media[media_id]["alt_text"] == "Обложка"

    def test_featured_missing_file_fails_before_any_write(self, wp, tmp_path):
        result, payload = upsert(
            {"slug": "x", "title": "T", "content": "c", "featured": {"path": str(tmp_path / "нет.jpg")}}
        )

        assert result.exit_code == 1
        assert payload["ok"] is False
        assert wp.writes == [] and wp.posts == {} and wp.media == {}

    def test_featured_existing_media_id(self, wp):
        media_id = wp.add_media()

        result, payload = upsert({"slug": "x", "title": "T", "content": "c", "featured": {"id": media_id}})

        assert result.exit_code == 0, result.output
        assert payload["featured_media"] == media_id
        assert len(wp.media) == 1

    def test_featured_needs_exactly_one_source(self, wp, tmp_path):
        result, payload = upsert(
            {"slug": "x", "title": "T", "content": "c", "featured": {"id": 1, "url": "https://a/b.jpg"}}
        )

        assert result.exit_code == 2
        assert wp.requests == []


class TestUpsertInput:
    def test_invalid_json(self, wp):
        result, payload = upsert("{не json")

        assert result.exit_code == 2
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert "JSON" in payload["error"]["message"]
        assert wp.requests == []

    def test_title_required_on_create(self, wp):
        result, payload = upsert({"slug": "x", "content": "c"})

        assert result.exit_code == 2
        assert "title" in payload["error"]["message"]
        assert wp.requests == []

    def test_slug_or_id_required(self, wp):
        result, payload = upsert({"title": "T", "content": "c"})

        assert result.exit_code == 2
        assert wp.requests == []

    def test_unknown_field_is_rejected(self, wp):
        result, payload = upsert({"slug": "x", "title": "T", "contnet": "c"})

        assert result.exit_code == 2
        assert "contnet" in payload["error"]["message"]
        assert wp.requests == []

    def test_input_must_be_object(self, wp):
        result, payload = upsert("[1, 2]")

        assert result.exit_code == 2
        assert wp.requests == []

    def test_input_is_required(self, wp):
        result, payload = invoke(["post", "upsert", "--site", SITE])

        assert result.exit_code == 2


class TestAuth:
    @pytest.mark.parametrize("missing", [ENV_USER, ENV_TOKEN])
    def test_missing_env_is_auth_missing_without_network(self, wp, monkeypatch, missing):
        monkeypatch.delenv(missing)

        result, payload = upsert({"slug": "x", "title": "T", "content": "c"})

        assert result.exit_code == 3
        assert payload["error"]["code"] == "AUTH_MISSING"
        assert ENV_USER in payload["error"]["message"] or ENV_USER in payload["error"].get("action", "")
        assert ENV_TOKEN in payload["error"]["message"] or ENV_TOKEN in payload["error"].get("action", "")
        assert wp.requests == []

    def test_dotenv_is_not_a_credential_source(self, wp, monkeypatch, tmp_path):
        monkeypatch.delenv(ENV_TOKEN)
        (tmp_path / ".env").write_text(f"{ENV_TOKEN}={TOKEN}\nWP_DOMAIN={SITE}\n", encoding="utf-8")

        result, payload = upsert({"slug": "x", "title": "T", "content": "c"})

        assert result.exit_code == 3
        assert payload["error"]["code"] == "AUTH_MISSING"

    def test_wrong_password_is_auth_failed_and_not_leaked(self, wp, monkeypatch):
        monkeypatch.setenv(ENV_TOKEN, "wrong-secret-9876")

        result, payload = upsert({"slug": "x", "title": "T", "content": "c"})

        assert result.exit_code == 3
        assert payload["error"]["code"] == "AUTH_FAILED"
        assert "wrong-secret-9876" not in result.output
        assert "wrong-secret-9876" not in (result.stderr or "")

    @pytest.mark.parametrize("flag", ["--password", "--username"])
    def test_credentials_in_argv_are_refused(self, wp, flag):
        result, payload = invoke(["post", "get", "--id", "1", "--site", SITE, flag, "secret"])

        assert result.exit_code == 2
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"
        assert "WP_API_TOKEN" in json.dumps(payload["error"], ensure_ascii=False)
        assert "secret" not in result.output.replace(flag, "")
        assert wp.requests == []


class TestGet:
    def test_get_returns_raw_fields_and_same_hash_as_upsert(self, wp):
        _, created = upsert({"slug": "x", "title": "Т", "content": "<p>raw</p>", "seo": {"title": "S"}})

        result, payload = get_post(created["id"])

        assert result.exit_code == 0, result.output
        assert payload["id"] == created["id"]
        assert payload["slug"] == "x"
        assert payload["title"] == "Т"
        assert payload["content"] == "<p>raw</p>"
        assert payload["status"] == "draft"
        assert payload["url"] == created["url"]
        assert payload["modified"] == created["modified"]
        assert payload["seo"]["title"] == "S"
        assert payload["featured_media"] == 0
        assert payload["content_hash"] == created["content_hash"]

    def test_requests_edit_context(self, wp):
        post_id = wp.add_post("x")

        get_post(post_id)

        [request] = wp.requests
        assert "context=edit" in str(request.url)

    def test_missing_id_is_not_found(self, wp):
        result, payload = get_post(9999)

        assert result.exit_code == 1
        assert payload["error"]["code"] == "NOT_FOUND"

    def test_type_without_seo_gives_null_seo(self, wp):
        wp.seo_types = set()
        post_id = wp.add_post("x")

        result, payload = get_post(post_id)

        assert result.exit_code == 0, result.output
        assert payload["seo"] is None


class TestMediaAttach:
    def test_attach_uploaded_file(self, wp, tmp_path):
        post_id = wp.add_post("x", status="publish")
        image = tmp_path / "f.jpg"
        image.write_bytes(b"\xff\xd8fake")

        result, payload = attach(post_id, "--path", str(image), "--alt", "Фото")

        assert result.exit_code == 0, result.output
        [media_id] = list(wp.media)
        assert payload["media_id"] == media_id
        assert payload["post_id"] == post_id
        assert payload["source_url"].endswith("/f.jpg")
        assert wp.posts[post_id]["featured_media"] == media_id
        assert wp.posts[post_id]["status"] == "publish"
        assert wp.media[media_id]["alt_text"] == "Фото"

    def test_attach_existing_media_without_upload(self, wp):
        post_id = wp.add_post("x")
        media_id = wp.add_media("old.jpg")

        result, payload = attach(post_id, "--media-id", str(media_id))

        assert result.exit_code == 0, result.output
        assert payload["media_id"] == media_id
        assert wp.posts[post_id]["featured_media"] == media_id
        assert len(wp.media) == 1
        assert not any(r.url.path == "/wp-json/wp/v2/media" for r in wp.writes)

    def test_missing_post_is_not_found_before_upload(self, wp, tmp_path):
        image = tmp_path / "f.jpg"
        image.write_bytes(b"\xff\xd8fake")

        result, payload = attach(9999, "--path", str(image))

        assert result.exit_code == 1
        assert payload["error"]["code"] == "NOT_FOUND"
        assert wp.media == {}

    def test_missing_file_fails_without_writes(self, wp, tmp_path):
        post_id = wp.add_post("x")

        result, payload = attach(post_id, "--path", str(tmp_path / "нет.jpg"))

        assert result.exit_code == 1
        assert wp.writes == []

    def test_exactly_one_source(self, wp):
        post_id = wp.add_post("x")

        result, payload = attach(post_id, "--media-id", "1", "--url", "https://a/b.jpg")

        assert result.exit_code == 2
        assert wp.requests == []


class TestServerMode:
    def test_no_log_files_in_cwd(self, wp, tmp_path):
        post_id = wp.add_post("x")

        result, _ = get_post(post_id)

        assert result.exit_code == 0, result.output
        assert list(Path(tmp_path).iterdir()) == []
