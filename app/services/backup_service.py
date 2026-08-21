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
) -> Path:
    """Write a timestamped, consistent copy of ``db_path`` into ``target_dir``.

    Uses the SQLite online-backup API (``Connection.backup``), which is safe to
    run against a live, WAL-mode database. Returns the written backup path.
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
    return dst
