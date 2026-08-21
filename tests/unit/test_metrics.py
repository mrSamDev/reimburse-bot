"""Tests for the in-process metrics registry."""

from app.utils import metrics


def test_metrics_inc_and_get():
    metrics.reset_metrics()
    metrics.inc("processed", 3)
    metrics.inc("failed")
    m = metrics.get_metrics()
    assert m["processed"] == 3
    assert m["failed"] == 1
    assert m["delivered"] == 0


def test_reset_metrics():
    metrics.reset_metrics()
    metrics.inc("processed", 5)
    metrics.reset_metrics()
    assert metrics.get_metrics()["processed"] == 0
