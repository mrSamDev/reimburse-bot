"""Vision AI provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class ReceiptExtraction(BaseModel):
    """Raw structured output expected from a vision provider.

    Mirrors what the AI is asked to return. Values are still untrusted until
    validated; this model performs only shape/type validation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    merchant_name: str | None = None
    transaction_date: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal | None = None
    confidence: float = 0.0
    notes: str = ""

    @field_validator("subtotal", "tax", "discount", "total", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        try:
            return Decimal(str(v))
        except Exception as exc:
            raise ValueError(f"invalid monetary value {v!r}") from exc

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0


class AIProviderError(Exception):
    """Raised when a vision provider fails (timeout, transport, parse)."""


class AIRateLimitError(AIProviderError):
    """Raised when the provider rate-limits the request (HTTP 429).

    ``retry_after`` carries the server's parsed ``Retry-After`` hint in seconds
    (if available). ``kind`` is the API's error type/code (e.g. ``"tokens"`` for
    a tokens-per-minute limit) so retry logic can pick an appropriate wait.
    """

    def __init__(
        self,
        message: str = "rate limited",
        retry_after: float | None = None,
        kind: str | None = None,
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.kind = kind


class ReceiptVisionProvider(ABC):
    """Interface every vision provider must implement.

    ``extract_receipt`` receives the path to a normalized image and returns a
    :class:`ReceiptExtraction`. Business logic never depends on a concrete
    provider.
    """

    @abstractmethod
    def extract_receipt(self, image_path: str | Path) -> ReceiptExtraction:
        """Extract structured receipt data from ``image_path``."""
