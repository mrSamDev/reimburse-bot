"""Strict receipt domain model."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Common 3-letter currency codes accepted in V1.
CURRENCY_RE = r"^[A-Z]{3}$"
DEFAULT_CURRENCY = "AED"


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce int/float/str to Decimal without silent precision loss errors."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        # Use Decimal(str(...)) to avoid binary-float artifacts.
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid monetary value: {value!r}") from None


class Receipt(BaseModel):
    """A single extracted receipt.

    All monetary values are ``Decimal``. Only ``merchant_name`` and ``total``
    are required; everything else is nullable because a real receipt may not
    carry subtotal/tax/discount, and the AI is instructed never to invent data.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    merchant_name: str
    transaction_date: str | None = None
    currency: str = DEFAULT_CURRENCY
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal
    confidence: float = 1.0
    review_required: bool = False
    notes: str = ""
    source_file_id: str = ""

    @field_validator("merchant_name", mode="before")
    @classmethod
    def _nonempty_merchant(cls, v: Any) -> str:
        if v is None:
            raise ValueError("merchant_name is required")
        s = str(v).strip()
        if not s:
            raise ValueError("merchant_name cannot be empty")
        return s

    @field_validator("transaction_date", mode="before")
    @classmethod
    def _clean_date(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, v: Any) -> str:
        c = str(v or "").strip().upper()
        if not c:
            return DEFAULT_CURRENCY
        return c

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        import re

        if not re.fullmatch(CURRENCY_RE, v):
            raise ValueError(f"Invalid currency: {v!r}")
        return v

    @field_validator("subtotal", "tax", "discount", "total", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Decimal | None:
        return _to_decimal(v)

    @field_validator("subtotal", "tax", "discount", "total")
    @classmethod
    def _nonnegative(cls, v: Decimal | None, info: Any) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return v

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: Any) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid confidence: {v!r}") from None
        return f

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {v}")
        return v

    @field_validator("notes", "source_file_id", mode="before")
    @classmethod
    def _str_fields(cls, v: Any) -> str:
        return "" if v is None else str(v)

    def display_date(self) -> str:
        return self.transaction_date or "—"


class Batch(BaseModel):
    """A collection of receipts processed together."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    receipts: list[Receipt] = Field(default_factory=list)
    currency_totals: dict[str, Decimal] = Field(default_factory=dict)
    processed_count: int = 0
    failed_count: int = 0
    review_count: int = 0

    def add(self, receipt: Receipt) -> None:
        self.receipts.append(receipt)
        self.currency_totals[receipt.currency] = (
            self.currency_totals.get(receipt.currency, Decimal("0"))
            + receipt.total
        )

    @property
    def total(self) -> Decimal:
        return sum((r.total for r in self.receipts), Decimal("0"))

    def currencies(self) -> list[str]:
        return sorted(self.currency_totals)

    def totals_by_currency(self) -> dict[str, Decimal]:
        """Return currency totals rounded to 2 decimal places."""
        return {c: t.quantize(Decimal("0.01")) for c, t in self.currency_totals.items()}
