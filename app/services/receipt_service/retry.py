"""Retry-with-backoff around a single AI extraction call.

Pure and provider-agnostic: the only dependency on the pipeline is the
budget-exhaustion exception type, so this module can be reasoned about and
tested in isolation.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.ai.base import (
    AIProviderError,
    AIRateLimitError,
    ReceiptExtraction,
    ReceiptVisionProvider,
)
from app.utils import metrics

from .types import BudgetExceededError

# Upper bound (seconds) for rate-limit backoff, applied before jitter.
MAX_RATE_LIMIT_DELAY = 60.0


@dataclass
class _CallBudget:
    """Per-run counter of paid AI extraction calls.

    Incremented synchronously in the event loop (never across an ``await``), so
    it is safe to share across the concurrent receipts of one batch.
    """

    max_calls: int
    used: int = 0

    def acquire(self) -> bool:
        """Reserve one call; return False when the budget is exhausted."""
        if self.used >= self.max_calls:
            return False
        self.used += 1
        return True


async def _extract_with_retry(
    provider: ReceiptVisionProvider,
    image_path: str | Path,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    deadline: float | None = None,
    budget: _CallBudget | None = None,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _rand: Callable[[], float] = random.random,
    _now: Callable[[], float] | None = None,
) -> ReceiptExtraction:
    """Extract a receipt, retrying transient provider failures with backoff.

    Only ``AIProviderError`` (possibly transient) is retried; validation errors
    aren't. ``_sleep``/``_random``/``_now`` are injectable for tests.
    """
    loop = asyncio.get_running_loop()
    now = _now or loop.time

    async def _sleep_backoff(delay: float, exc: BaseException) -> None:
        """Sleep ``delay`` unless the deadline is already spent; else stop."""
        if deadline is None:
            await _sleep(delay)
            return
        remaining = deadline - now()
        if remaining <= 0:
            raise exc
        await _sleep(min(delay, remaining))

    for attempt in range(max_attempts):
        if budget is not None and not budget.acquire():
            raise BudgetExceededError(
                f"AI call budget ({budget.max_calls}) exhausted for this run"
            )
        metrics.inc("ai_calls")
        try:
            return await asyncio.to_thread(provider.extract_receipt, image_path)
        except AIRateLimitError as exc:
            metrics.inc("ai_rate_limited")
            if attempt == max_attempts - 1:
                raise
            kind = getattr(exc, "kind", None)
            if kind == "tokens":
                # TPM window is 60s; the tiny Retry-After is useless, wait it out.
                delay = MAX_RATE_LIMIT_DELAY
            else:
                retry_after = getattr(exc, "retry_after", None)
                delay = retry_after if retry_after is not None else base_delay * (2**attempt)
            delay = min(delay, MAX_RATE_LIMIT_DELAY)
            delay = delay * (1 + _rand())
            await _sleep_backoff(delay, exc)
        except AIProviderError as exc:
            metrics.inc("ai_errors")
            if attempt == max_attempts - 1:
                raise
            # Full jitter: sleep in [0, base_delay * (attempt+1)].
            delay = base_delay * (attempt + 1) * _rand()
            await _sleep_backoff(delay, exc)
    raise AIProviderError("unreachable")  # pragma: no cover
