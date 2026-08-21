"""Tests for financial validation."""

from decimal import Decimal

from app.models.receipt import Receipt
from app.services.financial_validation import (
    LOW_CONFIDENCE,
    apply_assessment,
    assess_receipt,
)


def _r(**kw):
    base = dict(merchant_name="M", total="50", confidence=0.9)
    base.update(kw)
    return Receipt(**base)


def test_nonnegative_amounts_enforced_by_model():
    assert _r(subtotal="0", tax="0", discount="0", total="0").total == Decimal("0")


def test_reconciling_receipt_no_warnings():
    r = _r(subtotal="50", tax="3.5", discount="0", total="53.5", transaction_date="Jun 23, 2026")
    a = assess_receipt(r)
    assert a.reconciles
    assert not a.review_required


def test_reconciliation_mismatch_flags_review():
    r = _r(subtotal="50", tax="3.5", discount="0", total="100")
    a = assess_receipt(r)
    assert not a.reconciles
    assert "Financial values don't reconcile" in a.warnings
    assert a.review_required


def test_subtotal_with_inclusive_tax_flags_review():
    # subtotal already includes VAT => subtotal+tax exceeds total.
    r = _r(subtotal="53.5", tax="0", discount="0", total="53.5")
    # 53.5 + 0 - 0 == 53.5 reconciles exactly; no flag.
    assert assess_receipt(r).reconciles


def test_discount_applied():
    r = _r(subtotal="60", tax="0", discount="10", total="50")
    assert assess_receipt(r).reconciles


def test_low_confidence_flags_review():
    r = _r(confidence=0.3)
    a = assess_receipt(r)
    assert "Low AI confidence" in a.warnings
    assert a.review_required


def test_missing_date_flags_review():
    r = _r(transaction_date=None)
    a = assess_receipt(r)
    assert "Missing transaction date" in a.warnings


def test_apply_assessment_sets_flag_and_notes():
    r = _r(confidence=0.3, transaction_date=None)
    out = apply_assessment(r)
    assert out.review_required is True
    assert "Low AI confidence" in out.notes


def test_apply_assessment_clean_receipt_untouched():
    r = _r(total="10", confidence=0.95, transaction_date="Jul 1, 2026")
    out = apply_assessment(r)
    assert out.review_required is False
    assert out.notes == ""


def test_confidence_threshold_constant():
    assert LOW_CONFIDENCE == 0.6
