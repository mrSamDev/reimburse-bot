"""Reference-output verification against the supplied 14-page report."""

import json
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from app.models.receipt import Batch, Receipt
from app.services.pdf_service import generate_report

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "expected_output" / "reference_receipts.json"


def _load_reference() -> Batch:
    data = json.loads(FIXTURE.read_text())
    batch = Batch()
    for item in data:
        batch.add(
            Receipt(
                merchant_name=item["merchant_name"],
                transaction_date=item.get("transaction_date"),
                currency=item.get("currency", "AED"),
                total=item["total"],
                confidence=item.get("confidence", 1.0),
            )
        )
    return batch


def test_reference_total_is_1304():
    batch = _load_reference()
    assert batch.total == Decimal("1304.00")
    assert batch.currency_totals["AED"] == Decimal("1304.00")


def test_reference_pdf_header_and_period(tmp_path):
    batch = _load_reference()
    out = generate_report(batch, tmp_path / "reference.pdf", period="July Expenses")
    reader = PdfReader(str(out))
    assert len(reader.pages) > 1  # 28 receipts => multiple pages
    text = " ".join((p.extract_text() or "") for p in reader.pages)
    text = " ".join(text.split())
    assert "Heading Travel Expenses" in text
    assert "July Expenses" in text


def test_reference_pdf_contains_descriptions_and_amounts(tmp_path):
    batch = _load_reference()
    out = generate_report(batch, tmp_path / "reference.pdf")
    text = " ".join((p.extract_text() or "") for p in PdfReader(str(out)).pages)
    text = " ".join(text.split())
    for name in ("Ride with Sazzad", "VILLAGE HUB GROCERY DUBAI", "Bolt", "Tasleem"):
        assert name in text
    assert "AED 53.50" in text
    assert "AED 73.00" in text


def test_reference_pdf_final_total(tmp_path):
    batch = _load_reference()
    out = generate_report(batch, tmp_path / "reference.pdf")
    text = " ".join((p.extract_text() or "") for p in PdfReader(str(out)).pages)
    text = " ".join(text.split())
    assert "Total: AED 1,304.00" in text
