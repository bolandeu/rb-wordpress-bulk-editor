"""
Тесты LLMClient: текст идёт через llmkit, картинки — своим вызовом.

Сети нет: вызов модели подменён фейковым `completion` (тестовый шов
`LlmKit(completion=...)`), запрос за картинкой — фейковой requests.Session.
"""

import base64
from types import SimpleNamespace

import pytest
from llmkit import LlmKit

from src.utils.llm_client import LLMClient, LLMError

CONFIG_TEMPLATE = """
[providers.openrouter]
kind = "openrouter"

[levels]
cheap = ["openrouter/openai/gpt-4o-mini"]
strong = ["openrouter/anthropic/claude-sonnet-4"]

[defaults]
level = "cheap"
temperature = 0.0
timeout = 5
max_tokens = 100

[cache]
enabled = false
path = "{cache}"

[usage]
path = "{usage}"

[retry]
timeout = 0
rate_limit = 0
server = 0
invalid_output = 0
"""


class Timeout(Exception):
    """Классификация ошибок в llmkit идёт по имени класса — этого хватает."""


def response(text: str = "ответ"):
    """Минимальный объект ответа в форме litellm.ModelResponse."""
    return SimpleNamespace(
        model="fake",
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


class FakeCompletion:
    """Подменяет litellm.acompletion: по шагу на вызов, последний повторяется."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def __call__(self, **kwargs):
        step = self.script[min(len(self.calls), len(self.script) - 1)]
        self.calls.append(kwargs)
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Фабрика: LLMClient с llmkit, у которого подменён вызов модели."""
    config = tmp_path / "config.toml"
    config.write_text(
        CONFIG_TEMPLATE.format(cache=tmp_path / "cache.sqlite", usage=tmp_path / "usage.sqlite"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLMKIT_CONFIG", str(config))
    monkeypatch.setenv("OPENROUTER_API_KEY", "ключ-openrouter")

    def factory(script):
        fake = FakeCompletion(script)
        kit = LlmKit(completion=fake, cache=False, tags={"tool": "wp-bulk-editor"})
        return LLMClient(kit=kit), fake

    return factory


async def test_generate_uses_llmkit(client):
    llm_client, fake = client([response("сгенерированный текст")])

    result = await llm_client.generate("Опиши услугу", models=["openrouter/openai/gpt-4o-mini"])

    assert result == "сгенерированный текст"
    assert len(fake.calls) == 1
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "Опиши услугу"}]


async def test_generate_chain_fallback(client):
    """Явный список моделей — это цепочка llmkit: упала первая, отвечает вторая."""
    llm_client, fake = client([Timeout("долго"), response("со второй")])

    result = await llm_client.generate(
        "Опиши услугу",
        models=["openrouter/openai/gpt-4o-mini", "openrouter/anthropic/claude-sonnet-4"],
    )

    assert result == "со второй"
    assert [call["model"] for call in fake.calls] == [
        "openrouter/openai/gpt-4o-mini",
        "openrouter/anthropic/claude-sonnet-4",
    ]


async def test_generate_level_resolves_chain_from_config(client):
    llm_client, fake = client([response("уровнем strong")])

    result = await llm_client.generate("Опиши услугу", level="strong")

    assert result == "уровнем strong"
    assert fake.calls[0]["model"] == "openrouter/anthropic/claude-sonnet-4"


async def test_generate_without_models_and_level_uses_default_level(client):
    """Ни --model, ни --level — llmkit берёт [defaults] level из своего конфига."""
    llm_client, fake = client([response("дефолтным уровнем")])

    result = await llm_client.generate("Опиши услугу")

    assert result == "дефолтным уровнем"
    assert fake.calls[0]["model"] == "openrouter/openai/gpt-4o-mini"


async def test_generate_raises_llm_error_when_all_models_failed(client):
    llm_client, _ = client([Timeout("долго")])

    with pytest.raises(LLMError) as excinfo:
        await llm_client.generate(
            "Опиши услугу",
            models=["openrouter/openai/gpt-4o-mini", "openrouter/anthropic/claude-sonnet-4"],
        )

    assert "openrouter/openai/gpt-4o-mini" in str(excinfo.value)


async def test_generate_raises_llm_error_on_empty_answer(client):
    llm_client, _ = client([response("   ")])

    with pytest.raises(LLMError):
        await llm_client.generate("Опиши услугу", models=["openrouter/openai/gpt-4o-mini"])


# --- картинки: путь не изменился, llmkit тут не участвует ---


class FakeSession:
    """Фейковая requests.Session: отдаёт заранее заданный ответ на post()."""

    def __init__(self, payload, calls):
        self.trust_env = True
        self._payload = payload
        self._calls = calls

    def post(self, url, headers=None, json=None, timeout=None):
        self._calls.append({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(status_code=200, text="", json=lambda: self._payload)


async def test_generate_image_uses_own_endpoint(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n-fake"
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    payload = {"choices": [{"message": {"images": [{"image_url": {"url": data_url}}]}}]}
    calls = []
    monkeypatch.setattr(
        "src.utils.llm_client.requests.Session",
        lambda: FakeSession(payload, calls),
    )

    # kit намеренно не задан: обращение к llmkit на этом пути — ошибка
    fake_auth = "секрет"
    llm_client = LLMClient(endpoint="https://api.example/v1/chat/completions", token=fake_auth)
    image_bytes, ext = await llm_client.generate_image("нарисуй кота", models=["image-model"])

    assert (image_bytes, ext) == (png, "png")
    assert calls[0]["url"] == "https://api.example/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer секрет"
    assert calls[0]["json"]["model"] == "image-model"
    assert llm_client._kit is None
