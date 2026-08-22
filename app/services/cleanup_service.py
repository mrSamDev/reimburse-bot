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


def sweep_orphaned_requests(
    temp_root: str | Path, *, age_seconds: float = 600.0
) -> int:
    """Remove leftover ``request_*`` directories from a crashed process.

    Normal runs clean up in ``finally``; a hard kill (SIGKILL/OOM) leaves
    orphans behind. Run once at startup so a previous crash never fills the
    temp filesystem. Returns how many directories were removed.

    Only directories whose mtime is older than ``age_seconds`` are removed, so
    a fresh in-flight request (e.g. from another instance sharing ``TEMP_DIR``)
    is never destroyed. ``age_seconds=0`` removes everything (the historical
    behavior, safe for a per-container tmpfs).
    """
    import time

    root = Path(temp_root)
    removed = 0
    if not root.exists():
        return 0
    cutoff = time.time() - age_seconds
    for entry in root.iterdir():
        if not (entry.is_dir() and entry.name.startswith("request_")):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            cleanup_request_dir(entry)
            removed += 1
    return removed
