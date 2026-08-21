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


def sweep_orphaned_requests(temp_root: str | Path) -> int:
    """Remove leftover ``request_*`` directories from a crashed process.

    Normal runs clean up in ``finally``; a hard kill (SIGKILL/OOM) leaves
    orphans behind. Run once at startup so a previous crash never fills the
    temp filesystem. Returns how many directories were removed.
    """
    root = Path(temp_root)
    removed = 0
    if not root.exists():
        return 0
    # Known risk: this deletes every request_* dir at startup with no age check
    # or instance scoping. Safe today because Docker uses a per-container tmpfs;
    # if TEMP_DIR is ever a shared volume across instances, one instance's
    # startup sweep would destroy another's in-flight request dirs.
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.startswith("request_"):
            cleanup_request_dir(entry)
            removed += 1
    return removed
