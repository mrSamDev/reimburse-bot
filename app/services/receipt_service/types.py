"""Shared exceptions, result types, and filesystem helpers for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.models.receipt import Batch, Receipt


class ProcessingError(Exception):
    """Raised when processing cannot produce any usable report."""


class BudgetExceededError(ProcessingError):
    """Raised when the per-run AI call budget is exhausted mid-batch.

    Subclass of :class:`ProcessingError` so it aborts the whole batch rather
    than being treated as a single-receipt failure.
    """


@dataclass
class ProcessingResult:
    batch: Batch
    out_pdf_path: Path
    request_id: str
    request_base: Path
    processed_count: int = 0
    failed_count: int = 0
    review_count: int = 0
    receipt_failures: list[dict] = field(default_factory=list)


@dataclass
class _ReceiptOutcome:
    receipt: Receipt | None = None
    failed: bool = False
    reason: str = ""


def make_request_base(temp_root: str | Path, request_id: str) -> Path:
    base = Path(temp_root) / f"request_{request_id}"
    (base / "input").mkdir(parents=True, exist_ok=True)
    (base / "normalized").mkdir(parents=True, exist_ok=True)
    (base / "output").mkdir(parents=True, exist_ok=True)
    return base


def _pdf_filename(request_id: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"reimbursement_{date}_{request_id}.pdf"
