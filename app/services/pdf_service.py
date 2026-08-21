"""ReportLab reimbursement report generator.

Layout mirrors the original ``Reimburse/pdf_report.py`` implementation: a
compact 3-column table (Image | Receipt | Amount) with slim page margins
(20mm sides, 18mm top/bottom) so far less page space is wasted on padding.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
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

# Column widths for the 3-column table (A4 portrait usable width ~170mm).
COL_WIDTHS = [38 * mm, 92 * mm, 40 * mm]  # Image | Receipt | Amount

# Default display width (points) for embedded receipt thumbnails.
IMAGE_WIDTH_PT = 95.0


def _money(value: Decimal) -> str:
    return f"{Decimal(value):,.2f}"


def _scaled_image(path: Path, target_width_pt: float) -> Image:
    """Return a ReportLab ``Image`` flowable scaled to ``target_width_pt`` while
    preserving the source aspect ratio."""
    reader = ImageReader(str(path))
    iw, ih = reader.getSize()
    width = target_width_pt
    height = width * ih / iw if iw else width
    return Image(str(path), width=width, height=height)


def _build_styles() -> dict[str, ParagraphStyle]:
    """Return the paragraph styles used by the report."""
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "RepH1", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, spaceAfter=6, textColor=colors.HexColor("#1a1a1a"),
    )
    h2 = ParagraphStyle(
        "RepH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=15, leading=18, spaceBefore=4, spaceAfter=10,
        textColor=colors.HexColor("#333333"),
    )
    cell_left = ParagraphStyle(
        "CellLeft", fontName="Helvetica", fontSize=9, leading=11, alignment=TA_LEFT,
    )
    cell_right = ParagraphStyle(
        "CellRight", fontName="Helvetica", fontSize=9, leading=11, alignment=TA_RIGHT,
    )
    header_left = ParagraphStyle(
        "HdrLeft", parent=cell_left, fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    header_right = ParagraphStyle(
        "HdrRight", parent=cell_right, fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    total = ParagraphStyle(
        "Total", fontName="Helvetica-Bold", fontSize=12, leading=15,
        alignment=TA_RIGHT, spaceBefore=10,
    )
    return {
        "h1": h1, "h2": h2,
        "cell_left": cell_left, "cell_right": cell_right,
        "header_left": header_left, "header_right": header_right,
        "total": total,
    }


def _build_table(
    rows: list[tuple[str, str, str | None]],
    image_width_pt: float,
    styles: dict,
) -> Table:
    """Build the 3-column (Image | Receipt | Amount) table for ``rows``.

    ``rows`` items are ``(receipt_text, amount_display, image_path_or_None)``.
    """
    table_data = [[
        Paragraph("", styles["header_left"]),
        Paragraph("Receipt", styles["header_left"]),
        Paragraph("Amount", styles["header_right"]),
    ]]

    for receipt_text, amount_display, image_path in rows:
        img_cell = ""
        if image_path and Path(image_path).exists():
            try:
                img_cell = _scaled_image(Path(image_path), image_width_pt)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not embed image %s: %s", image_path, exc)
                img_cell = Paragraph("(image unavailable)", styles["cell_left"])
        elif image_path:
            img_cell = Paragraph("(image missing)", styles["cell_left"])
        table_data.append([
            img_cell,
            Paragraph(escape(receipt_text), styles["cell_left"]),
            Paragraph(escape(amount_display), styles["cell_right"]),
        ])

    table = Table(table_data, colWidths=COL_WIDTHS, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a4a4a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    return table


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
    text-only rows. Margins and the 3-column layout match the original
    ``Reimburse/pdf_report.py`` implementation.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image_map = image_map or {}
    st = _build_styles()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Reimbursement Report Generator",
    )

    story = [Paragraph(title, st["h1"])]
    if period:
        story.append(Paragraph(period, st["h2"]))

    # Row shape expected by the table: (receipt text, amount, image path).
    rows: list[tuple[str, str, str | None]] = []
    for receipt in batch.receipts:
        image_path = image_map.get(receipt.source_file_id)
        amount_display = f"{receipt.currency} {_money(receipt.total)}"
        rows.append((receipt.merchant_name, amount_display, image_path))

    story.append(_build_table(rows, IMAGE_WIDTH_PT, st))
    story.append(Spacer(1, 4 * mm))

    # Totals section. Never combine different currencies into one total.
    if len(batch.currencies()) == 1:
        currency = batch.currencies()[0]
        total = batch.currency_totals[currency]
        story.append(Paragraph(f"Total: {currency} {_money(total)}", st["total"]))
    else:
        for currency in batch.currencies():
            total = batch.currency_totals[currency]
            story.append(Paragraph(f"{currency} Total: {_money(total)}", st["total"]))

    doc.build(story)
    logger.info("PDF report written to %s", out_path)
    return out_path
