"""Derive the report period subtitle from receipt transaction dates."""

from __future__ import annotations

import calendar
from datetime import datetime

from app.models.receipt import Receipt

# DD/MM/YYYY for numeric dates, matching the reference report; specific first.
_DATE_FORMATS = (
    "%a, %b %d, %Y",  # Tue, Jun 23, 2026
    "%a %b %d, %Y",   # Tue Jun 23, 2026
    "%b %d, %Y",      # Jul 2, 2026
    "%B %d, %Y",      # July 2, 2026
    "%d %b %Y",       # 16 Jul 2026
    "%d %B %Y",       # 16 July 2026
    "%d/%m/%Y",       # 29/06/2026
    "%d.%m.%Y",
    "%d-%m-%Y",
)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def derive_report_period(receipts: list[Receipt]) -> str:
    """Return the most frequent month (ties -> latest) or "" if unparseable."""
    counts: dict[tuple[int, int], int] = {}
    for receipt in receipts:
        dt = _parse_date(receipt.transaction_date)
        if dt is None:
            continue
        key = (dt.year, dt.month)
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return ""

    (year, month), _ = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return f"{calendar.month_name[month]} Expenses"
