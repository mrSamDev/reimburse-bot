"""ReportLab reimbursement report generator."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.receipt import Batch

logger = logging.getLogger(__name__)

PAGE = A4
MARGIN = 40 * mm
USABLE = PAGE[0] - 2 * MARGIN
COL_AMOUNT = 130
COL_RECEIPT = USABLE - COL_AMOUNT
IMAGE_MAX_WIDTH = COL_RECEIPT - 8
IMAGE_MAX_HEIGHT = 110


def _money(value: Decimal) -> str:
    return f"{Decimal(value):,.2f}"


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=20, leading=24,
            alignment=TA_LEFT, spaceAfter=2,
        ),
        "period": ParagraphStyle(
            "period", fontName="Helvetica", fontSize=14, leading=18,
            textColor=colors.grey, spaceAfter=16,
        ),
        "desc": ParagraphStyle(
            "desc", fontName="Helvetica-Bold", fontSize=11, leading=14,
            alignment=TA_LEFT,
        ),
        "date": ParagraphStyle(
            "date", fontName="Helvetica", fontSize=9, leading=12,
            textColor=colors.HexColor("#555555"),
        ),
        "warning": ParagraphStyle(
            "warning", fontName="Helvetica-Oblique", fontSize=8.5, leading=11,
            textColor=colors.HexColor("#b45309"),
        ),
        "amount": ParagraphStyle(
            "amount", fontName="Helvetica-Bold", fontSize=11, leading=14,
            alignment=TA_RIGHT,
        ),
        "head": ParagraphStyle(
            "head", fontName="Helvetica-Bold", fontSize=11, leading=14,
            textColor=colors.HexColor("#333333"),
        ),
        "total": ParagraphStyle(
            "total", fontName="Helvetica-Bold", fontSize=12, leading=16,
            alignment=TA_RIGHT,
        ),
        "subtotal": ParagraphStyle(
            "subtotal", fontName="Helvetica", fontSize=10, leading=14,
            alignment=TA_RIGHT, textColor=colors.grey,
        ),
    }


def _scaled_image(path: str | Path) -> Image:
    """Create an Image flowable preserving aspect ratio within caps."""
    from PIL import Image as PILImage

    with PILImage.open(path) as im:
        w, h = im.size
    aspect = h / w if w else 1.0
    width = IMAGE_MAX_WIDTH
    height = width * aspect
    if height > IMAGE_MAX_HEIGHT:
        height = IMAGE_MAX_HEIGHT
        width = height / aspect if aspect else IMAGE_MAX_WIDTH
    return Image(str(path), width=width, height=height)


def _receipt_cell(receipt, image_path: str | Path | None) -> list:
    parts = []
    if image_path and Path(image_path).exists():
        parts.append(_scaled_image(image_path))
        parts.append(Spacer(1, 4))
    parts.append(Paragraph(receipt.merchant_name, _styles()["desc"]))
    if receipt.transaction_date:
        parts.append(Paragraph(receipt.transaction_date, _styles()["date"]))
    if receipt.review_required:
        reasons = receipt.notes or "Review required"
        parts.append(Paragraph(f"⚠ Review required — {reasons}", _styles()["warning"]))
    return parts


def generate_report(
    batch: Batch,
    out_path: str | Path,
    *,
    title: str = "Heading Travel Expenses",
    period: str = "",
    image_map: dict[str, str] | None = None,
) -> Path:
    """Generate a reimbursement PDF and return its path.

    ``image_map`` maps ``source_file_id`` -> normalized image path so the
    original receipt image is embedded. Receipts without an image render as
    text-only rows.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image_map = image_map or {}
    st = _styles()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=PAGE,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Reimbursement Report",
    )

    story = []
    story.append(Paragraph(title, st["title"]))
    if period:
        story.append(Paragraph(period, st["period"]))

    # Table: header row + one row per receipt. repeatRows=1 reprints the
    # "Receipt | Amount" header on every page.
    header = [
        [Paragraph("Receipt", st["head"])],
        [Paragraph("Amount", st["head"])],
    ]
    data_rows = []
    for receipt in batch.receipts:
        image_path = image_map.get(receipt.source_file_id)
        row = [
            _receipt_cell(receipt, image_path),
            [Paragraph(f"{receipt.currency} {_money(receipt.total)}", st["amount"])],
        ]
        data_rows.append(row)

    table = Table([header] + data_rows, colWidths=[COL_RECEIPT, COL_AMOUNT], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("LINEBELOW", (0, 0), (1, 0), 1, colors.HexColor("#999999")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)

    # Totals section. Never combine different currencies into one total.
    story.append(Spacer(1, 18))
    if len(batch.currencies()) == 1:
        currency = batch.currencies()[0]
        total = batch.currency_totals[currency]
        story.append(Paragraph(f"Total: {currency} {_money(total)}", st["total"]))
    else:
        for currency in batch.currencies():
            total = batch.currency_totals[currency]
            story.append(Paragraph(f"{currency} Total: {_money(total)}", st["subtotal"]))

    doc.build(story)
    return out_path
