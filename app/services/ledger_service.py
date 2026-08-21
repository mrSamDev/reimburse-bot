"""Durable audit ledger for accepted receipts (SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    file_id TEXT UNIQUE,
    merchant_name TEXT NOT NULL,
    transaction_date TEXT,
    currency TEXT NOT NULL,
    total TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'accepted',
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReceiptLedger:
    """Append-only audit trail of accepted receipts, deduplicated by ``file_id``.

    ``total`` is stored as TEXT (the string form of a ``Decimal``) so no
    precision is lost, matching the app-wide Decimal discipline. Idempotent by
    ``file_id``: re-running a batch after a crash never double-counts a receipt.

    A fresh connection is opened per operation and WAL mode is enabled, which is
    safe to call from worker threads via ``asyncio.to_thread``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def insert(self, entry: dict[str, Any]) -> bool:
        """Insert one receipt; True if newly inserted, False on duplicate file_id."""
        row = (
            int(entry["user_id"]),
            entry.get("file_id") or None,
            entry["merchant_name"],
            entry.get("transaction_date"),
            entry["currency"],
            str(entry["total"]),
            int(bool(entry.get("review_required", False))),
            entry.get("status", "accepted"),
            entry["request_id"],
            entry.get("created_at") or _utc_now(),
        )
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO receipts "
                "(user_id, file_id, merchant_name, transaction_date, currency, "
                " total, review_required, status, request_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        finally:
            conn.close()

    def all(self) -> list[dict[str, Any]]:
        """Return every receipt, newest-last, with ``total`` as ``Decimal``."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM receipts ORDER BY id").fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM receipts LIMIT 0").description]
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            if d.get("total") is not None:
                d["total"] = Decimal(d["total"])
            out.append(d)
        return out
