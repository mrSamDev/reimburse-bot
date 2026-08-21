"""ReportLab reimbursement report generator."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

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
COL_IMAGE = 118
COL_CAPTION = USABLE - COL_IMAGE
IMAGE_MAX_WIDTH = COL_IMAGE - 8


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
            "desc", fontName="Helvetica", fontSize=10, leading=13,
            alignment=TA_LEFT,
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
    """Create an Image flowable scaled to the receipt column, aspect preserved."""
    from PIL import Image as PILImage

    with PILImage.open(path) as im:
        w, h = im.size
    aspect = h / w if w else 1.0
    width = IMAGE_MAX_WIDTH
    height = width * aspect
    return Image(str(path), width=width, height=height)


def _caption_cell(receipt) -> Paragraph:
    """One-line caption: merchant (bold), optional date (grey), amount inline."""
    name = escape(receipt.merchant_name)
    caption = f"<b>{name}</b>"
    if receipt.transaction_date:
        caption += f' <font color="#6b7280">{escape(receipt.transaction_date)}</font>'
    caption += f'<br/><b>{escape(receipt.currency)} {_money(receipt.total)}</b>'
    return Paragraph(caption, _styles()["desc"])


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
    # "Receipt" header on every page.
    header = [[Paragraph("Receipt", st["head"])], []]
    data_rows = []
    for receipt in batch.receipts:
        image_path = image_map.get(receipt.source_file_id)
        image_flowable = (
            _scaled_image(image_path)
            if image_path and Path(image_path).exists()
            else ""
        )
        data_rows.append([image_flowable, _caption_cell(receipt)])

    table = Table([header] + data_rows, colWidths=[COL_IMAGE, COL_CAPTION], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("LINEBELOW", (0, 0), (1, 0), 1, colors.HexColor("#999999")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)

    # Totals section. Never combine different currencies into one total.
    story.append(Spacer(1, 14))
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
