"""Guaranteed cleanup for request-scoped temporary data."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_request_dir(base: str | Path) -> None:
    """Remove an entire request directory tree, ignoring errors.

    Intended to run from a ``finally`` block so temporary images, normalized
    images and generated PDFs never leak even on unexpected failures.
    """
    p = Path(base)
    if not p.exists():
        return
    try:
        shutil.rmtree(p)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("cleanup of %s failed: %s", p, exc)
