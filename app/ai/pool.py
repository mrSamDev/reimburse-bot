"""Provider pool: run multiple vision providers with load-balancing/fallback.

Lets OpenAI and Ollama Cloud be used at the same time. ``extract_receipt`` runs
via ``asyncio.to_thread`` in the processing pipeline, so the round-robin counter
is guarded by a ``threading.Lock``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.ai.base import AIProviderError, ReceiptExtraction, ReceiptVisionProvider

logger = logging.getLogger(__name__)

# Confidence below this triggers a fallback in the "priority" strategy.
LOW_CONFIDENCE = 0.5


class ProviderPool(ReceiptVisionProvider):
    """Distribute receipt extraction across multiple providers.

    ``strategy``:
      - ``"round_robin"``: cycle providers evenly (throughput). On failure of the
        chosen provider, fall back to the others.
      - ``"priority"``: always try ``primary`` first; fall back to the rest on
        failure or low confidence.
    """

    def __init__(
        self,
        providers: list[ReceiptVisionProvider],
        *,
        strategy: str = "round_robin",
        primary: ReceiptVisionProvider | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ProviderPool needs at least one provider")
        self._providers = list(providers)
        self._strategy = strategy
        self._primary = primary
        self._rr = 0
        self._lock = threading.Lock()

    def _ordered(self) -> list[ReceiptVisionProvider]:
        if self._strategy == "priority" and self._primary is not None:
            rest = [p for p in self._providers if p is not self._primary]
            return [self._primary, *rest]
        return self._providers

    def extract_receipt(self, image_path: str | Path) -> ReceiptExtraction:
        if self._strategy == "priority":
            return self._extract_priority(image_path)
        return self._extract_round_robin(image_path)

    def _extract_round_robin(self, image_path: str | Path) -> ReceiptExtraction:
        with self._lock:
            start = self._rr % len(self._providers)
            self._rr += 1
        last_exc: AIProviderError | None = None
        for i in range(len(self._providers)):
            provider = self._providers[(start + i) % len(self._providers)]
            try:
                return provider.extract_receipt(image_path)
            except AIProviderError as exc:
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
        last_exc: AIProviderError | None = None
        last_result: ReceiptExtraction | None = None
        for provider in ordered:
            try:
                result = provider.extract_receipt(image_path)
                last_result = result
                if result.confidence >= LOW_CONFIDENCE:
                    return result
                logger.info(
                    "low confidence %.2f from %s",
                    result.confidence,
                    type(provider).__name__,
                )
            except AIProviderError as exc:
                last_exc = exc
                logger.info(
                    "provider %s failed: %s", type(provider).__name__, exc
                )
        if last_result is not None:
            return last_result
        if last_exc is not None:
            raise last_exc
        raise AIProviderError("all providers failed")  # pragma: no cover - providers non-empty
