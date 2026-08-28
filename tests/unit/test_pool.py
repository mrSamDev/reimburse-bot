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


# ---- Circuit breaker (Fix #3) ---------------------------------------------


def test_circuit_opens_after_consecutive_failures():
    """A provider that fails N times in a row gets skipped (circuit opens)."""
    a, b = _fail("a"), _ok("b")
    pool = ProviderPool(
        [a, b], strategy="priority", primary=a,
        failure_threshold=3, cooldown_seconds=60,
    )
    # 3 extractions — each tries a first (fails) then falls back to b.
    for _ in range(3):
        pool.extract_receipt("x")
    # After 3 consecutive failures, a's circuit is open.
    assert a.calls == 3
    # Now a should be skipped entirely; only b is called.
    pool.extract_receipt("x")
    assert a.calls == 3  # not called again
    assert b.calls == 4


def test_circuit_resets_on_success():
    """A successful call resets the failure counter."""
    class _Recoverable(ReceiptVisionProvider):
        def __init__(self):
            self.calls = 0
            self.fail_until = 2

        def extract_receipt(self, image_path):
            self.calls += 1
            if self.calls <= self.fail_until:
                raise AIProviderError("transient")
            return ReceiptExtraction(merchant_name="a", total="10", confidence=0.9)

    a = _Recoverable()
    b = _ok("b")
    pool = ProviderPool(
        [a, b], strategy="priority", primary=a,
        failure_threshold=3, cooldown_seconds=60,
    )
    # Call 1: a fails (1), b succeeds.
    pool.extract_receipt("x")
    # Call 2: a fails (2), b succeeds.
    pool.extract_receipt("x")
    # Call 3: a succeeds (3) — resets the breaker.
    pool.extract_receipt("x")
    assert a.calls == 3
    # Now a has 0 failures. It should be tried again on the next call.
    pool.extract_receipt("x")
    assert a.calls == 4  # a was called (not skipped)


def test_circuit_closes_after_cooldown():
    """After the cooldown window, the provider is tried again."""
    a, b = _fail("a"), _ok("b")
    now = [100.0]
    pool = ProviderPool(
        [a, b], strategy="priority", primary=a,
        failure_threshold=2, cooldown_seconds=30,
        _now=lambda: now[0],
    )
    # 2 failures → circuit opens (until t=130).
    pool.extract_receipt("x")
    pool.extract_receipt("x")
    assert a.calls == 2
    # a is skipped while the circuit is open.
    pool.extract_receipt("x")
    assert a.calls == 2
    # After cooldown, a is tried again.
    now[0] = 131.0
    pool.extract_receipt("x")
    assert a.calls == 3  # a was retried


def test_all_circuits_open_tries_anyway():
    """If every provider's circuit is open, try them all (better than nothing)."""
    a, b = _fail("a"), _fail("b")
    now = [100.0]
    pool = ProviderPool(
        [a, b], strategy="priority", primary=a,
        failure_threshold=1, cooldown_seconds=60,
        _now=lambda: now[0],
    )
    # Both fail once → both circuits open.
    with pytest.raises(AIProviderError):
        pool.extract_receipt("x")
    assert a.calls == 1
    assert b.calls == 1
    # Now both circuits are open, but we still try (and fail).
    with pytest.raises(AIProviderError):
        pool.extract_receipt("x")
    assert a.calls == 2  # tried despite open circuit
    assert b.calls == 2


def test_circuit_breaker_default_threshold():
    """The default circuit breaker threshold is 3 consecutive failures."""
    pool = ProviderPool([_ok("a"), _ok("b")], strategy="round_robin")
    assert pool._breaker_threshold == 3
    assert pool._breaker_cooldown == 60.0
