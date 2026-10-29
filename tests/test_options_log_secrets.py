"""
Tests: значения опций сайта и тела запросов не попадают в лог.

Файловый sink логгера всегда DEBUG, поэтому любой logger.debug с данными
оказывается в <project>/logs/*.log. Сайт подменён httpx.MockTransport.
"""

import httpx
import pytest
from loguru import logger

from src.config import WordPressConfig
from src.providers.wordpress import WordPressHttpProvider
from src.wordpress.client import WordPressClient
from src.wordpress.options import OptionsEndpoint

SMTP_PASSWORD = "s3cret-123"
B24_WEBHOOK = "https://x/rest/3/tok-456/"
SITE_OPTIONS = {"smtp_password": SMTP_PASSWORD, "b24_webhook": B24_WEBHOOK}


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=SITE_OPTIONS)


@pytest.fixture
def config():
    return WordPressConfig.from_credentials("https://example.com", "admin", "app-pass")


@pytest.fixture
def log_text():
    """Весь текст лога, пойманный sink-ом уровня DEBUG."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    yield lambda: "".join(messages)
    logger.remove(sink_id)


async def test_options_get_and_update_keep_values_out_of_log(config, log_text):
    endpoint = OptionsEndpoint(WordPressClient(config, transport=httpx.MockTransport(_handler)))

    fetched = await endpoint.get()
    await endpoint.update(SITE_OPTIONS)

    assert fetched == SITE_OPTIONS  # вывод команды не меняется
    text = log_text()
    assert SMTP_PASSWORD not in text
    assert "tok-456" not in text
    assert "smtp_password" in text
    assert "b24_webhook" in text


@pytest.mark.parametrize("verb", ["post", "put"])
async def test_provider_write_verbs_keep_values_out_of_log(config, log_text, verb):
    provider = WordPressHttpProvider(config, transport=httpx.MockTransport(_handler))

    await getattr(provider, verb)("/wp-json/x/v1/y", {"password": SMTP_PASSWORD})

    text = log_text()
    assert SMTP_PASSWORD not in text
    assert "password" in text
