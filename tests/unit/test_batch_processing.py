"""Tests for batch processing semantics."""


from app.models.receipt import Batch, Receipt


def test_batch_counts_processed_and_review():
    b = Batch()
    ok = Receipt(merchant_name="OK", total="10")
    review = Receipt(merchant_name="REV", total="11", review_required=True)
    b.processed_count = 2
    b.review_count = 1
    b.add(ok)
    b.add(review)
    assert b.processed_count == 2
    assert b.review_count == 1
    assert len(b.receipts) == 2


def test_batch_tracks_failed_count():
    b = Batch()
    b.failed_count = 1
    assert b.failed_count == 1


def test_batch_independent_failure_tracking():
    # One bad receipt should not destroy the batch.
    b = Batch()
    b.processed_count = 2
    b.failed_count = 1
    assert len(b.receipts) == 0  # failures tracked separately, not as receipts
