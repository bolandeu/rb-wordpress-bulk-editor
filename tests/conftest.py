"""
Shared fixtures for WordPress Bulk Editor tests.
"""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_media_folder(tmp_path):
    """Temporary media folder with sample files."""
    media = tmp_path / "media"
    media.mkdir()
    (media / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (media / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    return media


@pytest.fixture
def tmp_excel_file(tmp_path):
    """Temporary .xlsx file (empty but valid path)."""
    f = tmp_path / "data.xlsx"
    f.write_bytes(b"")
    return f


@pytest.fixture
def sample_wp_post_response():
    """Sample WordPress REST API post response."""
    return {
        "id": 42,
        "title": {"rendered": "Hello World"},
        "content": {"rendered": "<p>Body text</p>"},
        "excerpt": {"rendered": "Short excerpt"},
        "slug": "hello-world",
        "status": "publish",
        "link": "https://example.com/hello-world",
        "categories": [3, 5],
        "tags": [10],
        "featured_media": 99,
        "date": "2024-01-15T10:00:00",
        "modified": "2024-01-16T12:00:00",
    }


@pytest.fixture
def taxonomy_cache():
    """Sample taxonomy ID→name cache."""
    return {
        3: "News",
        5: "Tutorials",
        10: "python",
        11: "django",
    }


# ── Контракты потребителей (tests/test_contracts.py, tests/contracts/fixtures.yaml) ──

CONTRACT_DATA = Path(__file__).parent / "contracts" / "data"


UNREACHABLE_SITE = "unreachable.example.com"  # fixture_site_unreachable


def _contract_site(monkeypatch):
    """Фальшивый WordPress для `run`-кейсов вместо сети; data/ — рабочая папка.

    Хост UNREACHABLE_SITE — «сайт лежит»: транспорт бросает httpx.ConnectError
    (как при отказе сети / TLS), паузы ретраев обнулены — без реальной сети.
    """
    import asyncio

    import httpx

    from src.cli import post_commands
    from src.wordpress.client import WordPressClient
    from tests.fake_wp import FakeWordPress

    site = FakeWordPress(seo_types={"posts"})
    site.add_post("existing-post", status="draft")  # 101 — fixture_post_id
    site.add_post("existing-draft", status="draft")  # 102
    site.add_post("published-post", status="publish")  # 103
    site.add_media("old.jpg")  # 104 — fixture_media_id

    def handler(request):
        if request.url.host == UNREACHABLE_SITE:
            raise httpx.ConnectError(f"[Errno 61] Connection refused ({UNREACHABLE_SITE})", request=request)
        return site.handler(request)

    real_sleep = asyncio.sleep

    async def no_wait(delay, *args, **kwargs):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        post_commands,
        "WordPressClient",
        lambda config: WordPressClient(config, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.chdir(CONTRACT_DATA)
    return site


@pytest.fixture
def contract_offline(monkeypatch):
    """Офлайн-данные без доступов: кейсы, которые обязаны упасть до сайта."""
    return _contract_site(monkeypatch)


@pytest.fixture
def contract_wordpress(monkeypatch):
    """Офлайн-данные и фальшивые доступы WP_API_USER / WP_API_TOKEN."""
    from tests.fake_wp import ENV_TOKEN, ENV_USER, TOKEN, USER

    site = _contract_site(monkeypatch)
    monkeypatch.setenv(ENV_USER, USER)
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    return site
