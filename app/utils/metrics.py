"""Minimal in-process metrics registry (prerequisite for real observability).

Counters live in memory and are reset on process start. Safe for single-process
use; a future round can export them via an endpoint or push gateway.
"""

from __future__ import annotations

_METRICS: dict[str, int] = {
    "processed": 0,
    "review": 0,
    "failed": 0,
    "delivered": 0,
    "ai_calls": 0,
    "ai_errors": 0,
    "ai_rate_limited": 0,
}


def inc(name: str, n: int = 1) -> None:
    _METRICS[name] = _METRICS.get(name, 0) + n


def get_metrics() -> dict[str, int]:
    return dict(_METRICS)


def reset_metrics() -> None:
    for key in _METRICS:
        _METRICS[key] = 0
