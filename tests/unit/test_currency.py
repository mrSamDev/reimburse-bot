"""Tests for multi-currency totals and batch accounting."""

from decimal import Decimal

from app.models.receipt import Batch, Receipt


def _r(currency, total):
    return Receipt(merchant_name="M", currency=currency, total=str(total))


def test_single_currency_total():
    b = Batch()
    for v in ("53.50", "51.00", "49.00"):
        b.add(_r("AED", v))
    assert b.currency_totals["AED"] == Decimal("153.50")
    assert b.currencies() == ["AED"]


def test_two_currencies():
    b = Batch()
    b.add(_r("AED", "100.00"))
    b.add(_r("USD", "50.00"))
    b.add(_r("AED", "25.00"))
    assert b.currency_totals == {"AED": Decimal("125.00"), "USD": Decimal("50.00")}
    assert b.currencies() == ["AED", "USD"]


def test_three_currencies():
    b = Batch()
    b.add(_r("AED", "1"))
    b.add(_r("USD", "2"))
    b.add(_r("EUR", "3"))
    assert b.currencies() == ["AED", "EUR", "USD"]


def test_currency_specific_totals_never_mixed():
    # AED 1304 + USD 50 must NOT become a single total.
    b = Batch()
    b.add(_r("AED", "1304.00"))
    b.add(_r("USD", "50.00"))
    assert "AED" in b.currency_totals
    assert "USD" in b.currency_totals
    # No key should be a mixture of both currencies.
    assert b.currency_totals["AED"] == Decimal("1304.00")
    assert b.currency_totals["USD"] == Decimal("50.00")


def test_batch_total_property():
    b = Batch()
    b.add(_r("AED", "10"))
    b.add(_r("USD", "5"))
    assert b.total == Decimal("15")  # raw sum across receipts


def test_totals_by_currency_are_quantized():
    b = Batch()
    b.add(_r("AED", "10.005"))
    b.add(_r("USD", "1.999"))
    totals = b.totals_by_currency()
    assert totals["AED"] == Decimal("10.005").quantize(Decimal("0.01"))
    assert totals["USD"] == Decimal("1.999").quantize(Decimal("0.01"))
