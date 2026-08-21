"""Durable backup of the SQLite state/audit databases."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(
    db_path: str | Path,
    target_dir: str | Path,
    *,
    label: str = "backup",
    retention: int | None = None,
) -> Path:
    """Write a timestamped, consistent copy of ``db_path`` into ``target_dir``.

    Uses the SQLite online-backup API (``Connection.backup``), which is safe to
    run against a live, WAL-mode database. Returns the written backup path.

    When ``retention`` is given, the oldest backups for this database/label are
    pruned so only the ``retention`` newest copies are kept (per-prefix, so one
    db's backups never delete another's). ``None`` (default) keeps every backup.
    """
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"Database not found: {src}")
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = target / f"{src.stem}_{label}_{ts}.db"
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    if retention is not None and retention >= 1:
        _prune_backups(target, src.stem, label, retention)
    return dst


def _prune_backups(
    target_dir: Path,
    stem: str,
    label: str,
    retention: int,
) -> None:
    """Delete the oldest backups matching this ``stem_label`` prefix, keeping
    the ``retention`` newest. Filenames sort chronologically (ISO timestamp)."""
    prefix = f"{stem}_{label}_"
    backups = sorted(
        p for p in target_dir.glob(f"{prefix}*.db") if p.is_file()
    )
    for stale in backups[:-retention]:
        stale.unlink(missing_ok=True)
