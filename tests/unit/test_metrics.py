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


def test_observe_tracks_count_and_sum():
    metrics.reset_metrics()
    metrics.observe("receipt_processing_seconds", 1.5)
    metrics.observe("receipt_processing_seconds", 2.5)
    m = metrics.get_metrics()
    assert m["receipt_processing_seconds_count"] == 2
    assert m["receipt_processing_seconds_sum"] == 4.0


def test_observe_reset_zeroes_both():
    metrics.reset_metrics()
    metrics.observe("batch_processing_seconds", 9.0)
    metrics.reset_metrics()
    m = metrics.get_metrics()
    assert m["batch_processing_seconds_count"] == 0
    assert m["batch_processing_seconds_sum"] == 0


def test_error_class_counters_preseeded():
    metrics.reset_metrics()
    m = metrics.get_metrics()
    for name in ("timeout", "validation_error", "ai_error", "unexpected"):
        assert m[name] == 0


def test_error_class_counters_increment():
    metrics.reset_metrics()
    metrics.inc("timeout")
    metrics.inc("validation_error")
    metrics.inc("ai_error")
    metrics.inc("unexpected")
    m = metrics.get_metrics()
    assert m["timeout"] == 1
    assert m["validation_error"] == 1
    assert m["ai_error"] == 1
    assert m["unexpected"] == 1
