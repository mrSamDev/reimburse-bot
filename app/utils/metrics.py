"""Minimal in-process metrics registry (prerequisite for real observability).

Counters live in memory and are reset on process start. Safe for single-process
use; a future round can export them via an endpoint or push gateway.

The registry is guarded by a ``threading.Lock`` because it is written from the
asyncio event loop (``inc``/``observe``) but read from the health-server thread
(``get_metrics``). Without the lock, ``dict(_METRICS)`` can raise
``RuntimeError: dictionary changed size during iteration`` and the non-atomic
read-modify-write in ``inc`` can lose updates.
"""

from __future__ import annotations

import threading

_METRICS: dict[str, int | float] = {
    "processed": 0,
    "review": 0,
    "failed": 0,
    "delivered": 0,
    "ai_calls": 0,
    "ai_errors": 0,
    "ai_rate_limited": 0,
    # Per-receipt failure classes (recorded in receipt_service).
    "timeout": 0,
    "validation_error": 0,
    "ai_error": 0,
    "unexpected": 0,
}

_lock = threading.Lock()


def inc(name: str, n: int = 1) -> None:
    with _lock:
        _METRICS[name] = _METRICS.get(name, 0) + n


def observe(name: str, value: float) -> None:
    """Record a histogram-like sample: ``<name>_count``/``<name>_sum`` pair.

    Lets the health endpoint report durations (e.g. ``receipt_processing_seconds``)
    with a count and a total sum, so an operator can compute an average.
    """
    with _lock:
        _METRICS[f"{name}_count"] = _METRICS.get(f"{name}_count", 0) + 1
        _METRICS[f"{name}_sum"] = _METRICS.get(f"{name}_sum", 0.0) + value


def get_metrics() -> dict[str, int | float]:
    with _lock:
        return dict(_METRICS)


def reset_metrics() -> None:
    with _lock:
        for key in _METRICS:
            _METRICS[key] = 0
