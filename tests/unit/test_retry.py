"""Tests for transient AI failure retry/backoff with jitter."""

import asyncio

import pytest

from app.ai.base import AIProviderError, ReceiptExtraction
from app.services.receipt_service import _extract_with_retry

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
