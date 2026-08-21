"""Tests for the AI providers (stubbed transport)."""


import pytest

from app.ai.base import AIProviderError, ReceiptExtraction
from app.ai.ollama_provider import OllamaProvider, build_provider
from app.ai.openai_provider import OpenAIProvider, _extract_json
from app.config import Config
from tests.conftest import make_image


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        resp = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeChat:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)


def _cfg(**kw):
    base = dict(ai_provider="openai", openai_api_key="k", openai_model="gpt-4o-mini")
    base.update(kw)
    return Config(**base)


def test_extract_json_plain():
    obj = _extract_json('{"merchant_name": "M", "total": 10}')
    assert obj == {"merchant_name": "M", "total": 10}


def test_extract_json_markdown_wrapped():
    obj = _extract_json('```json\n{"total": 5}\n```')
    assert obj == {"total": 5}


def test_extract_json_invalid_raises():
    with pytest.raises(AIProviderError):
        _extract_json("not json at all")


def test_extract_json_bare_block():
    obj = _extract_json('prefix {"a": 1} suffix')
    assert obj == {"a": 1}


def test_build_provider_openai():
    p = build_provider(_cfg_openai())
    assert isinstance(p, OpenAIProvider)


def test_build_provider_ollama():
    p = build_provider(_cfg_ollama())
    assert isinstance(p, OllamaProvider)


def test_build_provider_unsupported():
    cfg = Config(ai_provider="openai", openai_api_key="k")
    cfg.ai_provider = "x"
    with pytest.raises(AIProviderError):
        build_provider(cfg)


def test_openai_extract_receipt(monkeypatch, tmp_path):
    img = make_image(tmp_path / "r.jpg", "JPEG")
    client = _FakeClient([_FakeCompletion('{"merchant_name":"Ride","total":53.5,"confidence":0.9}')])
    monkeypatch.setattr(OpenAIProvider, "_client", client, raising=False)

    # Build provider then swap client.
    provider = OpenAIProvider(_make_openai())
    provider._client = client
    result = provider.extract_receipt(img)
    assert isinstance(result, ReceiptExtraction)
    assert result.merchant_name == "Ride"
    assert result.total == 53.5


def test_openai_returns_unparseable_raises(monkeypatch, tmp_path):
    img = make_image(tmp_path / "r.jpg", "JPEG")
    client = _FakeClient([_FakeCompletion("no json here")])
    provider = OpenAIProvider(_make_openai())
    provider._client = client
    with pytest.raises(AIProviderError):
        provider.extract_receipt(img)


def test_ollama_provider_uses_openai_compatible_client(monkeypatch, tmp_path):
    img = make_image(tmp_path / "r.jpg", "JPEG")
    client = _FakeClient([_FakeCompletion('{"merchant_name":"Bolt","total":73}')])
    provider = OllamaProvider(_cfg_ollama())
    provider._client = client
    result = provider.extract_receipt(img)
    assert result.total == 73


def _make_openai():
    return _cfg_openai()


def _cfg_openai():
    return Config(ai_provider="openai", openai_api_key="k", openai_model="gpt-4o-mini")


def _cfg_ollama():
    return Config(ai_provider="ollama", ollama_base_url="http://localhost:11434/v1", ollama_model="llava")


def _make_client(responses):
    return _FakeClient(responses)
