"""Tests for transient AI failure retry/backoff with jitter."""

import asyncio

import pytest

from app.ai.base import (
    AIProviderError,
    AIRateLimitError,
    ReceiptExtraction,
)
from app.services.receipt_service import (
    BudgetExceededError,
    _CallBudget,
    _extract_with_retry,
)

_DEFAULT_EXC = AIProviderError("boom")


class _FlakyProvider:
    """Fails on the first `fail_for` calls, then succeeds."""

    def __init__(self, fail_for=1, exc=None):
        self.fail_for = fail_for
        self.exc = exc if exc is not None else _DEFAULT_EXC
        self.calls = 0

    def extract_receipt(self, image_path):
        self.calls += 1
        if self.calls <= self.fail_for:
            raise self.exc
        return ReceiptExtraction(merchant_name="M", total="10")


def test_retries_then_succeeds_with_jittered_backoff():
    sleeps = []
    rands = iter([0.5, 0.9])

    async def fake_sleep(delay):
        sleeps.append(delay)

    provider = _FlakyProvider(fail_for=2)
    result = asyncio.run(
        _extract_with_retry(
            provider, "/img.jpg", max_attempts=3, base_delay=1.0,
            _sleep=fake_sleep, _rand=lambda: next(rands),
        )
    )
    assert result.merchant_name == "M"
    # Two retries, each with full-jitter delay in (0, base*(attempt+1)].
    assert len(sleeps) == 2
    assert 0 < sleeps[0] <= 1.0 and sleeps[0] == 0.5
    assert 0 < sleeps[1] <= 2.0 and sleeps[1] == 1.8


def test_no_retry_needed_when_first_call_succeeds():
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    provider = _FlakyProvider(fail_for=0)
    asyncio.run(
        _extract_with_retry(provider, "/img.jpg", _sleep=fake_sleep)
    )
    assert provider.calls == 1
    assert sleeps == []


def test_deterministic_with_injected_rand():
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    # Force rand to 1.0 so delay == base*(attempt+1): no jitter, deterministic.
    provider = _FlakyProvider(fail_for=2)
    asyncio.run(
        _extract_with_retry(
            provider, "/img.jpg", max_attempts=3, base_delay=0.5,
            _sleep=fake_sleep, _rand=lambda: 1.0,
        )
    )
    assert sleeps == [0.5, 1.0]


def test_exhausts_attempts_and_raises():
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    provider = _FlakyProvider(fail_for=99)
    with pytest.raises(AIProviderError):
        asyncio.run(
            _extract_with_retry(provider, "/img.jpg", max_attempts=3, base_delay=0.5, _sleep=fake_sleep)
        )
    assert provider.calls == 3
    assert len(sleeps) == 2  # backoff between attempts, then give up


def test_single_attempt_does_not_retry():
    provider = _FlakyProvider(fail_for=1)
    with pytest.raises(AIProviderError):
        asyncio.run(_extract_with_retry(provider, "/img.jpg", max_attempts=1, base_delay=0))
    assert provider.calls == 1


def test_rate_limit_respects_retry_after():
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    # Server asks for 0.5s; jitter multiplies by (1 + rand).
    provider = _FlakyProvider(fail_for=1, exc=AIRateLimitError("rl", retry_after=0.5))
    result = asyncio.run(
        _extract_with_retry(
            provider, "/img.jpg", max_attempts=3, base_delay=1.0,
            _sleep=fake_sleep, _rand=lambda: 1.0,
        )
    )
    assert result.merchant_name == "M"
    assert sleeps == [1.0]  # 0.5 * (1 + 1.0)


def test_rate_limit_fallback_backoff_capped():
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    # No retry-after hint -> exponential fallback base_delay*(2**attempt),
    # capped at MAX_RATE_LIMIT_DELAY before jitter.
    from app.services.receipt_service import MAX_RATE_LIMIT_DELAY

    provider = _FlakyProvider(fail_for=2, exc=AIRateLimitError("rl"))
    asyncio.run(
        _extract_with_retry(
            provider, "/img.jpg", max_attempts=3, base_delay=50.0,
            _sleep=fake_sleep, _rand=lambda: 0.0,
        )
    )
    # attempt 0: 50*1=50 capped 50; attempt 1: 50*2=100 capped 60.
    assert sleeps == [50.0, MAX_RATE_LIMIT_DELAY]


def test_rate_limit_exhausts_attempts_and_raises():
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    provider = _FlakyProvider(fail_for=99, exc=AIRateLimitError("rl", retry_after=0.2))
    with pytest.raises(AIRateLimitError):
        asyncio.run(
            _extract_with_retry(
                provider, "/img.jpg", max_attempts=3, base_delay=1.0,
                _sleep=fake_sleep, _rand=lambda: 0.0,
            )
        )
    assert provider.calls == 3
    assert len(sleeps) == 2


def test_token_limit_waits_out_tpm_window():
    # TPM 429: tiny Retry-After is useless; must wait out the ~60s window.
    from app.services.receipt_service import MAX_RATE_LIMIT_DELAY

    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    provider = _FlakyProvider(
        fail_for=2, exc=AIRateLimitError("tpm", retry_after=0.38, kind="tokens")
    )
    asyncio.run(
        _extract_with_retry(
            provider, "/img.jpg", max_attempts=3, base_delay=1.0,
            _sleep=fake_sleep, _rand=lambda: 0.0,
        )
    )
    # Both retries wait the full TPM window (capped), not the 380ms hint.
    assert sleeps == [MAX_RATE_LIMIT_DELAY, MAX_RATE_LIMIT_DELAY]


def test_rate_limit_backoff_capped_to_deadline_and_stops_retrying():
    """A backoff sleep must not run past the per-receipt deadline; once the
    deadline is exhausted no further retries are scheduled (the enclosing
    ``wait_for`` would otherwise kill an already-spent sleep)."""
    sleeps = []
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    async def fake_sleep(delay):
        sleeps.append(delay)
        clock["t"] += delay

    provider = _FlakyProvider(
        fail_for=3, exc=AIRateLimitError("tpm", kind="tokens")
    )
    with pytest.raises(AIRateLimitError):
        asyncio.run(
            _extract_with_retry(
                provider, "/img.jpg", max_attempts=5, base_delay=1.0,
                deadline=10.0, _sleep=fake_sleep, _rand=lambda: 0.0, _now=now,
            )
        )
    # First TPM backoff (60s) is capped to the 10s remaining; after that the
    # deadline is spent so no further provider call is made.
    assert provider.calls == 2
    assert sleeps == [10.0]
    assert clock["t"] == 10.0


def test_ai_call_budget_exhausted_raises_and_stops():
    """Once the per-run AI call budget is spent, extraction stops instead of
    making another paid provider call."""
    provider = _FlakyProvider(fail_for=99)
    budget = _CallBudget(max_calls=2)
    with pytest.raises(BudgetExceededError):
        asyncio.run(
            _extract_with_retry(
                provider, "/img.jpg", max_attempts=5, base_delay=0, budget=budget
            )
        )
    assert provider.calls == 2
    assert budget.used == 2


def test_ai_call_budget_satisfied_on_success():
    provider = _FlakyProvider(fail_for=1)
    budget = _CallBudget(max_calls=5)
    result = asyncio.run(
        _extract_with_retry(
            provider, "/img.jpg", max_attempts=3, base_delay=0, budget=budget
        )
    )
    assert result.merchant_name == "M"
    assert provider.calls == 2
    assert budget.used == 2
