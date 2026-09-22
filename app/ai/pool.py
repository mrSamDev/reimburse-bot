"""Provider pool: run multiple vision providers with load-balancing/fallback.

Lets OpenAI and Ollama Cloud be used at the same time. ``extract_receipt`` runs
via ``asyncio.to_thread`` in the processing pipeline, so the round-robin counter
and circuit-breaker state are guarded by a ``threading.Lock``.

Each provider has a circuit breaker: after ``failure_threshold`` consecutive
failures it is skipped for ``cooldown_seconds``. This prevents a downed
provider from being retried on every receipt in every batch, burning the call
budget. When all providers are open, they are all tried anyway (better to
attempt and fail than to do nothing). A successful call resets the breaker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.ai.base import AIProviderError, ReceiptExtraction, ReceiptVisionProvider

logger = logging.getLogger(__name__)

# Confidence below this triggers a fallback in the "priority" strategy.
LOW_CONFIDENCE = 0.5

# Circuit breaker defaults.
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60.0


class ProviderPool(ReceiptVisionProvider):
    """Distribute receipt extraction across multiple providers.

    ``strategy``:
      - ``"round_robin"``: cycle providers evenly (throughput). On failure of the
        chosen provider, fall back to the others.
      - ``"priority"``: always try ``primary`` first; fall back to the rest on
        failure or low confidence.

    Circuit breaker: each provider tracks consecutive failures. After
    ``failure_threshold`` failures the provider is skipped for
    ``cooldown_seconds``. A successful extraction resets the counter.
    """

    def __init__(
        self,
        providers: list[ReceiptVisionProvider],
        *,
        strategy: str = "round_robin",
        primary: ReceiptVisionProvider | None = None,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        _now: Callable[[], float] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ProviderPool needs at least one provider")
        self._providers = list(providers)
        self._strategy = strategy
        self._primary = primary
        self._rr = 0
        self._lock = threading.Lock()
        self._breaker_threshold = failure_threshold
        self._breaker_cooldown = cooldown_seconds
        self._now = _now or time.monotonic
        # Circuit breaker state per provider (aligned with _providers list).
        self._failures: list[int] = [0] * len(self._providers)
        self._open_until: list[float] = [0.0] * len(self._providers)

    def _ordered(self) -> list[ReceiptVisionProvider]:
        if self._strategy == "priority" and self._primary is not None:
            rest = [p for p in self._providers if p is not self._primary]
            return [self._primary, *rest]
        return self._providers

    def extract_receipt(self, image_path: str | Path) -> ReceiptExtraction:
        if self._strategy == "priority":
            return self._extract_priority(image_path)
        return self._extract_round_robin(image_path)

    # ---- Circuit breaker helpers (must be called under self._lock) ---------

    def _is_open(self, idx: int, now: float) -> bool:
        return now < self._open_until[idx]

    def _record_success(self, idx: int) -> None:
        self._failures[idx] = 0
        self._open_until[idx] = 0.0

    def _record_failure(self, idx: int) -> None:
        self._failures[idx] += 1
        if self._failures[idx] >= self._breaker_threshold:
            self._open_until[idx] = self._now() + self._breaker_cooldown
            logger.info(
                "circuit opened for provider %d after %d consecutive failures",
                idx,
                self._failures[idx],
            )

    def _try_order(self, all_indices: list[int]) -> list[int]:
        """Return indices to try: skip open circuits, unless all are open."""
        now = self._now()
        closed = [i for i in all_indices if not self._is_open(i, now)]
        return closed if closed else all_indices

    # ---- Extraction strategies ---------------------------------------------

    def _extract_round_robin(self, image_path: str | Path) -> ReceiptExtraction:
        with self._lock:
            start = self._rr % len(self._providers)
            self._rr += 1
            all_indices = [
                (start + i) % len(self._providers) for i in range(len(self._providers))
            ]
            try_indices = self._try_order(all_indices)
        last_exc: AIProviderError | None = None
        for idx in try_indices:
            provider = self._providers[idx]
            try:
                result = provider.extract_receipt(image_path)
                with self._lock:
                    self._record_success(idx)
                return result
            except AIProviderError as exc:
                with self._lock:
                    self._record_failure(idx)
                last_exc = exc
                logger.info(
                    "provider %s failed, trying next: %s",
                    type(provider).__name__,
                    exc,
                )
        if last_exc is not None:
            raise last_exc
        raise AIProviderError("all providers failed")  # pragma: no cover - providers non-empty

    def _extract_priority(self, image_path: str | Path) -> ReceiptExtraction:
        ordered = self._ordered()
        # Map ordered providers to their indices for circuit-breaker tracking.
        idx_map = {p: i for i, p in enumerate(self._providers)}
        ordered_indices = [idx_map[p] for p in ordered]
        with self._lock:
            try_indices = self._try_order(ordered_indices)
        last_exc: AIProviderError | None = None
        last_result: ReceiptExtraction | None = None
        for idx in try_indices:
            provider = self._providers[idx]
            try:
                result = provider.extract_receipt(image_path)
                last_result = result
                with self._lock:
                    self._record_success(idx)
                if result.confidence >= LOW_CONFIDENCE:
                    return result
                logger.info(
                    "low confidence %.2f from %s",
                    result.confidence,
                    type(provider).__name__,
                )
            except AIProviderError as exc:
                with self._lock:
                    self._record_failure(idx)
                last_exc = exc
                logger.info(
                    "provider %s failed: %s", type(provider).__name__, exc
                )
        if last_result is not None:
            return last_result
        if last_exc is not None:
            raise last_exc
        raise AIProviderError("all providers failed")  # pragma: no cover - providers non-empty
