"""Tests for Receipt / Batch models."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.receipt import Batch, Receipt


def _r(**kw):
    base = dict(merchant_name="Ride", total="53.50")
    base.update(kw)
    return Receipt(**base)


def test_valid_receipt():
    r = _r(transaction_date="Tue, Jun 23, 2026", currency="AED", confidence=0.9)
    assert r.total == Decimal("53.50")
    assert isinstance(r.total, Decimal)
    assert r.merchant_name == "Ride"


def test_total_is_decimal_from_string():
    r = _r(total="1234.00")
    assert r.total == Decimal("1234.00")
    assert isinstance(r.total, Decimal)


def test_total_is_decimal_from_int():
    assert _r(total=10).total == Decimal("10")


def test_total_is_decimal_from_float_string_avoiding_binary_artifacts():
    r = _r(total=0.1)
    assert r.total == Decimal("0.1")


def test_missing_merchant_name():
    with pytest.raises(ValidationError):
        Receipt(merchant_name="", total="1")


def test_missing_total():
    with pytest.raises(ValidationError):
        Receipt(merchant_name="M")


def test_negative_total_rejected():
    with pytest.raises(ValidationError):
        _r(total="-5")


def test_negative_subtotal_rejected():
    with pytest.raises(ValidationError):
        _r(subtotal="-1")


def test_negative_discount_rejected():
    with pytest.raises(ValidationError):
        _r(discount="-1")


def test_invalid_currency_rejected():
    with pytest.raises(ValidationError):
        _r(currency="AEDX")


def test_currency_normalized_uppercase():
    assert _r(currency="usd").currency == "USD"


def test_invalid_confidence():
    with pytest.raises(ValidationError):
        _r(confidence=1.5)
    with pytest.raises(ValidationError):
        _r(confidence=-0.1)


def test_default_confidence_and_currency():
    r = _r()
    assert r.confidence == 1.0
    assert r.currency == "AED"


def test_optional_fields_nullable():
    r = _r(subtotal=None, tax=None, discount=None)
    assert r.subtotal is None


def test_receipt_decimal_precision():
    r = _r(subtotal="10.10", tax="1.05", total="11.15")
    assert r.subtotal + r.tax == r.total
    assert r.subtotal + r.tax == Decimal("11.15")


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _r(bogus="x")


def test_display_date_fallback():
    assert _r(transaction_date=None).display_date() == "—"
    assert _r(transaction_date="29/06/2026").display_date() == "29/06/2026"


def test_batch_add_single_currency():
    b = Batch()
    b.add(_r(total="53.50", currency="AED"))
    b.add(_r(total="51.00", currency="AED"))
    assert b.currency_totals["AED"] == Decimal("104.50")
    assert b.receipts[0].merchant_name == "Ride"


def test_batch_mixed_currency_totals():
    b = Batch()
    b.add(_r(total="100", currency="AED"))
    b.add(_r(total="50", currency="USD"))
    assert b.currency_totals["AED"] == Decimal("100")
    assert b.currency_totals["USD"] == Decimal("50")
    assert b.currencies() == ["AED", "USD"]
