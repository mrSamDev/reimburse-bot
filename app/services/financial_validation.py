"""Financial / business validation for extracted receipts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.receipt import Receipt

# A mismatch beyond a couple of cents flags a receipt for review.
RECONCILE_TOLERANCE = Decimal("0.02")
# Confidence below this triggers a review flag.
LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class FinancialAssessment:
    reconciles: bool
    warnings: list[str]

    @property
    def review_required(self) -> bool:
        return not self.reconciles or bool(self.warnings)


def assess_receipt(receipt: Receipt) -> FinancialAssessment:
    """Apply non-destructive checks to a receipt.

    Never raises for a reconciliation mismatch — that produces a review flag so
    a human can decide. Hard structural problems (negative amounts, invalid
    currency) are already rejected by the ``Receipt`` model.
    """
    warnings: list[str] = []

    # 1. Non-negative — guaranteed by model, but guard anyway.
    for name in ("subtotal", "tax", "discount", "total"):
        value = getattr(receipt, name)
        if value is not None and value < 0:
            warnings.append(f"{name} is negative")

    # 2. Reconciliation: subtotal + tax - discount ≈ total (when subtotal present).
    reconciles = True
    if receipt.subtotal is not None:
        expected = receipt.subtotal
        if receipt.tax is not None:
            expected += receipt.tax
        if receipt.discount is not None:
            expected -= receipt.discount
        if abs(expected - receipt.total) > RECONCILE_TOLERANCE:
            reconciles = False
            warnings.append("Financial values don't reconcile")

    # 3. Confidence.
    if receipt.confidence < LOW_CONFIDENCE:
        warnings.append("Low AI confidence")

    # 4. Missing transaction date.
    if not receipt.transaction_date:
        warnings.append("Missing transaction date")

    return FinancialAssessment(reconciles=reconciles, warnings=warnings)


def apply_assessment(receipt: Receipt) -> Receipt:
    """Mutate a receipt with review flags derived from the assessment."""
    assessment = assess_receipt(receipt)
    if assessment.review_required:
        receipt.review_required = True
        if receipt.notes:
            receipt.notes += " | " + "; ".join(assessment.warnings)
        else:
            receipt.notes = "; ".join(assessment.warnings)
    return receipt
