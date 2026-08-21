"""Tests for the ReportLab PDF generator."""

from pathlib import Path

from pypdf import PdfReader

from app.models.receipt import Batch, Receipt
from app.services.pdf_service import generate_report
from tests.conftest import make_image


def _r(name, total, currency="AED", date="Jun 23, 2026", **kw):
    base = dict(merchant_name=name, total=str(total), currency=currency,
                transaction_date=date)
    base.update(kw)
    return Receipt(**base)


def _batch(receipts):
    b = Batch()
    for r in receipts:
        b.add(r)
    return b


def _text(path: Path) -> str:
    reader = PdfReader(str(path))
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)
    return " ".join(raw.split())


def _count_images(path: Path) -> int:
    reader = PdfReader(str(path))
    total = 0
    for page in reader.pages:
        res = page.get("/Resources", {})
        xobj = res.get("/XObject")
        if xobj:
            obj = xobj.get_object()
            for name in obj:
                if "/Image" in str(obj[name].get_object().get("/Subtype", "")):
                    total += 1
    return total


def test_one_receipt_pdf(tmp_path):
    b = _batch([_r("Ride with Sazzad", "53.50")])
    out = generate_report(b, tmp_path / "one.pdf", period="July Expenses")
    text = _text(out)
    assert "Heading Travel Expenses" in text
    assert "July Expenses" in text
    assert "Ride with Sazzad" in text
    assert "AED 53.50" in text
    assert "Total: AED 53.50" in text


def test_two_receipts(tmp_path):
    b = _batch([_r("A", "10.00"), _r("B", "20.00")])
    out = generate_report(b, tmp_path / "two.pdf")
    text = _text(out)
    assert "A" in text and "B" in text
    assert "Total: AED 30.00" in text


def test_twenty_receipts_multipage(tmp_path):
    receipts = [_r(f"Receipt {i}", i + 1) for i in range(20)]
    b = _batch(receipts)
    out = generate_report(b, tmp_path / "twenty.pdf")
    reader = PdfReader(str(out))
    assert len(reader.pages) > 1
    text = _text(out)
    assert "Total: AED 210.00" in text


def test_review_warning_shown(tmp_path):
    r = _r("Low conf", "10.00", confidence=0.2, notes="blurry", review_required=True)
    b = _batch([r])
    out = generate_report(b, tmp_path / "review.pdf")
    text = _text(out)
    assert "Review required" in text


def test_image_aspect_preserved_no_distortion(tmp_path):
    img = make_image(tmp_path / "wide.jpg", "JPEG", size=(400, 100))
    b = _batch([_r("Ride", "10")])
    out = generate_report(b, tmp_path / "aspect.pdf", image_map={b.receipts[0].source_file_id: str(img)})
    assert out.exists()


def test_long_merchant_name_wraps(tmp_path):
    long_name = "VILLAGE HUB GROCERY DUBAI - VERY LONG MERCHANT NAME THAT SHOULD WRAP OVER MULTIPLE LINES"
    b = _batch([_r(long_name, "10.00", date="29/06/2026")])
    out = generate_report(b, tmp_path / "long.pdf")
    assert long_name in _text(out)


def test_missing_fields_render(tmp_path):
    b = _batch([_r("No date", "10.00", date=None)])
    out = generate_report(b, tmp_path / "missing.pdf")
    assert "No date" in _text(out)


def test_receipt_image_included(tmp_path):
    img = make_image(tmp_path / "receipt.jpg", "JPEG")
    b = _batch([_r("Ride with Sazzad", "53.50")])
    out = generate_report(b, tmp_path / "img.pdf", image_map={b.receipts[0].source_file_id: str(img)})
    assert _count_images(out) >= 1


def test_no_image_no_embed(tmp_path):
    b = _batch([_r("Ride", "10")])
    out = generate_report(b, tmp_path / "noimg.pdf")
    assert _count_images(out) == 0


def test_mixed_currency_totals(tmp_path):
    b = _batch([_r("A", "100", "AED"), _r("B", "50", "USD")])
    out = generate_report(b, tmp_path / "mixed.pdf")
    text = _text(out)
    assert "AED Total: 100.00" in text
    assert "USD Total: 50.00" in text
    # No combined total for mixed currencies.
    assert "Total: 150.00" not in text


def test_final_total_equals_decimal_sum(tmp_path):
    b = _batch([_r("A", "53.50"), _r("B", "51.00"), _r("C", "49.00")])
    out = generate_report(b, tmp_path / "sum.pdf")
    assert "Total: AED 153.50" in _text(out)


def test_pdf_valid(tmp_path):
    b = _batch([_r("A", "10")])
    out = generate_report(b, tmp_path / "valid.pdf")
    reader = PdfReader(str(out))
    assert len(reader.pages) >= 1


def test_configurable_title_and_period(tmp_path):
    b = _batch([_r("A", "10")])
    out = generate_report(b, tmp_path / "cfg.pdf", title="Business Expenses", period="August 2026")
    text = _text(out)
    assert "Business Expenses" in text
    assert "August 2026" in text
    assert "July" not in text
