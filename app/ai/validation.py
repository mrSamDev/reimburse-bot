"""Validation pipeline for raw AI output -> trusted Receipt."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.ai.base import AIProviderError, ReceiptExtraction
from app.models.receipt import Receipt
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
            currency=extraction.currency,
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


def validate_ai_result(raw: dict[str, Any], source_file_id: str) -> Receipt:
    """Turn raw AI JSON into a trusted, financially-validated Receipt.

    Hard failures (unusable shape, missing required fields, invalid amounts)
    raise :class:`AIValidationError`. Soft issues (reconciliation mismatch, low
    confidence, missing date) are folded into ``review_required``.
    """
    try:
        extraction = ReceiptExtraction(**raw)
    except ValidationError as exc:
        raise AIValidationError(f"AI output failed schema validation: {exc}") from exc
    return validate_extraction(extraction, source_file_id)
