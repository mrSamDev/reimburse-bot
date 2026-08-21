"""Unit tests for report-period derivation from receipt transaction dates."""

from decimal import Decimal

from app.models.receipt import Receipt
from app.services.report_period import derive_report_period


def _r(date):
    return Receipt(merchant_name="X", transaction_date=date, total=Decimal("1"))


def test_mixed_months_picks_dominant_month():
    # Matches the reference report: a few June receipts, most in July.
    receipts = [_r("Tue, Jun 23, 2026"), _r("29/06/2026")] + [
        _r("2026-07-16") for _ in range(6)
    ]
    assert derive_report_period(receipts) == "July Expenses"


def test_single_month():
    assert derive_report_period([_r("Jul 2, 2026"), _r("2026-07-16")]) == "July Expenses"


def test_tie_breaks_to_latest_month():
    # Equal counts across months -> the latest month wins.
    receipts = [
        _r("2026-06-10"),
        _r("2026-06-12"),
        _r("2026-07-05"),
        _r("2026-07-07"),
    ]
    assert derive_report_period(receipts) == "July Expenses"


def test_mixed_numeric_formats_parse():
    receipts = [
        _r("Tue, Jun 23, 2026"),
        _r("29/06/2026"),
        _r("2026-07-16"),
        _r("16 Jul 2026"),
        _r("11/07/2026"),
    ]
    # All formats parse; July appears three times, June twice -> July dominant.
    assert derive_report_period(receipts) == "July Expenses"


def test_no_parseable_dates_returns_empty():
    assert derive_report_period([_r(None), _r("not a date")]) == ""
    assert derive_report_period([]) == ""
