"""Tests for AI response validation pipeline (live path: validate_extraction)."""

import json
from pathlib import Path

import pytest

from app.ai.base import ReceiptExtraction
from app.ai.validation import AIValidationError, validate_extraction
from app.models.receipt import Receipt

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_responses"


def _load(name) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _validate(raw: dict, file_id: str = "f1") -> Receipt:
    """Mirror the pipeline: parse raw AI JSON into ReceiptExtraction, then validate."""
    return validate_extraction(ReceiptExtraction(**raw), file_id)


def test_valid_response_produces_clean_receipt():
    r = _validate(_load("valid_response.json"), "file_id_1")
    assert isinstance(r, Receipt)
    assert r.merchant_name == "Ride with Sazzad"
    assert r.total == 53.50
    assert r.source_file_id == "file_id_1"
    assert not r.review_required


def test_missing_total_rejected():
    with pytest.raises(AIValidationError):
        _validate(_load("missing_total.json"))


def test_invalid_date_is_string_and_accepted():
    # Dates are free-form text in V1; this fixture must not crash.
    r = _validate(_load("invalid_date.json"))
    assert r.transaction_date == "29/06/2026"


def test_negative_total_rejected():
    with pytest.raises(AIValidationError):
        _validate(_load("negative_total.json"))


def test_invalid_currency_rejected():
    with pytest.raises(AIValidationError):
        _validate(_load("invalid_currency.json"))


def test_malformed_json_not_a_dict_raises():
    # Simulates provider output that isn't a dict at all (no merchant_name).
    with pytest.raises(AIValidationError):
        _validate({"bad": True})


def test_low_confidence_flags_review():
    r = _validate(_load("low_confidence.json"))
    assert r.review_required is True


def test_missing_merchant_name_rejected():
    with pytest.raises(AIValidationError):
        _validate({"total": 10, "confidence": 0.9})


def test_never_accepts_raw_ai_dict():
    # Even a valid-ish dict is converted to a validated Receipt, never passed raw.
    r = _validate({"merchant_name": "X", "total": "5"})
    assert isinstance(r, Receipt)
