"""Validation pipeline for raw AI output -> trusted Receipt."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.ai.base import ReceiptExtraction
from app.models.receipt import DEFAULT_CURRENCY, Receipt
from app.services.financial_validation import apply_assessment

logger = logging.getLogger(__name__)


UNKNOWN_MERCHANT = "Unknown"


class AIValidationError(Exception):
    """Raised when AI output fails hard validation and cannot be used."""


def _resolve_merchant(extraction: ReceiptExtraction) -> tuple[str, str | None]:
    """Return ``(merchant_name, warning)``; warning is set when synthesized."""
    merchant = (extraction.merchant_name or "").strip()
    if merchant:
        return merchant, None
    # The AI sometimes misses the merchant; fall back so the receipt still imports.
    phone = (extraction.phone_number or "").strip()
    if phone:
        return phone, "Merchant name missing, used phone number"
    date = (extraction.transaction_date or "").strip()
    if date:
        return date, "Merchant name missing, used transaction date"
    return UNKNOWN_MERCHANT, "Merchant name missing"


def validate_extraction(extraction: ReceiptExtraction, source_file_id: str) -> Receipt:
    """Validate an already-parsed :class:`ReceiptExtraction`."""
    merchant, merchant_warning = _resolve_merchant(extraction)
    if extraction.total is None:
        raise AIValidationError("AI output missing total amount")
    try:
        receipt = Receipt(
            merchant_name=merchant,
            transaction_date=extraction.transaction_date,
            currency=extraction.currency or DEFAULT_CURRENCY,
            subtotal=extraction.subtotal,
            tax=extraction.tax,
            discount=extraction.discount,
            total=extraction.total,
            confidence=extraction.confidence,
            notes=extraction.notes or "",
            source_file_id=source_file_id,
        )
    except ValidationError as exc:
        raise AIValidationError(f"Receipt failed business validation: {exc}") from exc
    receipt = apply_assessment(receipt)
    if merchant_warning:
        receipt.review_required = True
        receipt.notes = (
            f"{merchant_warning} | {receipt.notes}" if receipt.notes else merchant_warning
        )
    return receipt
