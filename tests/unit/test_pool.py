"""Tests for the ProviderPool (multi-provider load-balancing/fallback)."""

import pytest

from app.ai.base import AIProviderError, ReceiptExtraction, ReceiptVisionProvider
from app.ai.ollama_provider import build_provider
from app.ai.pool import ProviderPool
from app.config import Config


class _FakeProvider(ReceiptVisionProvider):
    def __init__(self, name, result=None, error=None, confidence=0.9):
        self.name = name
        self.result = result
        self.error = error
        self.confidence = confidence
        self.calls = 0

    def extract_receipt(self, image_path):
        self.calls += 1
        if self.error:
            raise self.error
        if self.result is not None:
            return self.result
        return ReceiptExtraction(
            merchant_name=self.name, total="10", confidence=self.confidence
        )


def _ok(name, confidence=0.9):
    return _FakeProvider(name, confidence=confidence)


def _fail(name, exc=None):
    return _FakeProvider(name, error=exc or AIProviderError("boom"))


def test_round_robin_distributes():
    a, b = _ok("a"), _ok("b")
    pool = ProviderPool([a, b], strategy="round_robin")
    pool.extract_receipt("x")
    pool.extract_receipt("x")
    assert a.calls == 1
    assert b.calls == 1


def test_round_robin_falls_back_on_failure():
    a, b = _fail("a"), _ok("b")
    pool = ProviderPool([a, b], strategy="round_robin")
    result = pool.extract_receipt("x")
    assert result.merchant_name == "b"
    assert a.calls == 1
    assert b.calls == 1


def test_round_robin_all_fail_raises():
    a, b = _fail("a"), _fail("b")
    pool = ProviderPool([a, b], strategy="round_robin")
    with pytest.raises(AIProviderError):
        pool.extract_receipt("x")


def test_priority_tries_primary_first():
    primary, fallback = _ok("primary"), _ok("fallback")
    pool = ProviderPool([fallback, primary], strategy="priority", primary=primary)
    result = pool.extract_receipt("x")
    assert result.merchant_name == "primary"
    assert primary.calls == 1
    assert fallback.calls == 0


def test_priority_falls_back_on_failure():
    primary, fallback = _fail("primary"), _ok("fallback")
    pool = ProviderPool([primary, fallback], strategy="priority", primary=primary)
    result = pool.extract_receipt("x")
    assert result.merchant_name == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_priority_falls_back_on_low_confidence():
    primary, fallback = _ok("primary", confidence=0.2), _ok("fallback", confidence=0.9)
    pool = ProviderPool([primary, fallback], strategy="priority", primary=primary)
    result = pool.extract_receipt("x")
    assert result.merchant_name == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_priority_returns_last_result_if_all_low_confidence():
    primary, fallback = _ok("primary", confidence=0.2), _ok("fallback", confidence=0.3)
    pool = ProviderPool([primary, fallback], strategy="priority", primary=primary)
    result = pool.extract_receipt("x")
    assert result.merchant_name == "fallback"


def test_pool_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ProviderPool([])


def test_build_provider_pool_round_robin():
    cfg = Config(ai_provider="pool", openai_api_key="k", ollama_base_url="http://x")
    p = build_provider(cfg)
    assert isinstance(p, ProviderPool)


def test_build_provider_pool_priority():
    cfg = Config(
        ai_provider="pool", openai_api_key="k", ollama_base_url="http://x",
        ai_pool_strategy="priority", ai_pool_primary="ollama",
    )
    p = build_provider(cfg)
    assert isinstance(p, ProviderPool)
