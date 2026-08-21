"""Validation pipeline for raw AI output -> trusted Receipt."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.ai.base import ReceiptExtraction
from app.models.receipt import DEFAULT_CURRENCY, Receipt
from app.services.financial_validation import apply_assessment

logger = logging.getLogger(__name__)


class AIValidationError(Exception):
    """Raised when AI output fails hard validation and cannot be used."""


def validate_extraction(extraction: ReceiptExtraction, source_file_id: str) -> Receipt:
    """Validate an already-parsed :class:`ReceiptExtraction`."""
    merchant = extraction.merchant_name or ""
    if not merchant.strip():
        raise AIValidationError("AI output missing merchant_name")
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
    return apply_assessment(receipt)
