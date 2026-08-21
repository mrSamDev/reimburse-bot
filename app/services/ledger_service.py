"""Durable audit ledger for accepted + failed receipts (SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.services.backup_service import backup_database

_SCHEMA_VERSION = 2

# Ordered migrations keyed by target schema version.
_MIGRATIONS: dict[int, str] = {
    1: """
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
    CREATE INDEX IF NOT EXISTS idx_receipts_user_id ON receipts(user_id);
    CREATE INDEX IF NOT EXISTS idx_receipts_request_id ON receipts(request_id);
    """,
    2: """
    ALTER TABLE receipts ADD COLUMN failure_reason TEXT;
    ALTER TABLE receipts ADD COLUMN delivered_at TEXT;
    """,
}

# Columns written by ``insert`` / ``insert_failure`` (order must match VALUES).
_COLUMNS = (
    "user_id", "file_id", "merchant_name", "transaction_date", "currency",
    "total", "review_required", "status", "request_id", "created_at",
    "failure_reason",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal_or_none(value: Any) -> Decimal | None:
    """Convert a stored total back to Decimal, tolerating empty/unknown values."""
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


class ReceiptLedger:
    """Durable audit trail of receipts, deduplicated by ``file_id``.

    Accepted receipts carry a ``Decimal`` ``total`` and ``status='accepted'``;
    failed ones carry ``status='failed'`` with a ``failure_reason`` and no total.
    Idempotent by ``file_id`` so a re-run after a crash never double-counts.

    A fresh connection is opened per operation with WAL mode, safe to call from
    worker threads via ``asyncio.to_thread``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        """Bring the schema up to ``_SCHEMA_VERSION`` via ``PRAGMA user_version``."""
        conn = self._connect()
        try:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version in range(current + 1, _SCHEMA_VERSION + 1):
                sql = _MIGRATIONS.get(version)
                if sql:
                    conn.executescript(sql)
                conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        # Explicit busy timeout (10s) so writes wait out lock contention rather
        # than failing with "database is locked" under concurrent instances.
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _insert_row(self, row: tuple) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO receipts ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                row,
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def insert(self, entry: dict[str, Any]) -> bool:
        """Insert an accepted receipt; True if newly inserted, False on dup file_id."""
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
            None,  # failure_reason
        )
        return self._insert_row(row)

    def insert_failure(
        self,
        file_id: str,
        reason: str,
        *,
        request_id: str,
        user_id: int,
        created_at: str | None = None,
    ) -> bool:
        """Record a receipt that could not be processed (status='failed')."""
        row = (
            int(user_id),
            file_id or None,
            "",  # merchant unknown for a failed receipt
            None,
            "",  # currency unknown
            "",  # no total for a failed receipt
            0,
            "failed",
            request_id,
            created_at or _utc_now(),
            reason,
        )
        return self._insert_row(row)

    def mark_delivered(self, request_id: str, delivered_at: str | None = None) -> int:
        """Record that a report was delivered, for accepted receipts of a request.

        Failed rows are never marked delivered. Returns how many rows touched.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE receipts SET delivered_at = ? WHERE request_id = ? "
                "AND status = 'accepted' AND delivered_at IS NULL",
                (delivered_at or _utc_now(), request_id),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def backup(self, target_dir: str | Path, *, retention: int | None = None) -> Path:
        """Write a durable copy of the audit ledger DB into ``target_dir``."""
        return backup_database(self._db_path, target_dir, label="receipts", retention=retention)

    def summary(self) -> dict[str, int]:
        """Aggregate counts for a period-less reconciliation."""
        conn = self._connect()
        try:
            accepted = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE status = 'accepted'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE status = 'failed'"
            ).fetchone()[0]
            delivered = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE delivered_at IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        return {"accepted": accepted, "failed": failed, "delivered": delivered,
                "total": accepted + failed}

    def count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        finally:
            conn.close()

    def by_user(self, user_id: int) -> list[dict[str, Any]]:
        """Return every receipt for one user, oldest-first, ``total`` as Decimal."""
        return self._fetch("WHERE user_id = ? ORDER BY id", (int(user_id),))

    def all(self) -> list[dict[str, Any]]:
        """Return every receipt, oldest-first, ``total`` as Decimal."""
        return self._fetch("ORDER BY id", ())

    def _fetch(self, where: str, params: tuple) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM receipts {where}", params).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM receipts LIMIT 0").description]
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(zip(cols, r, strict=True))
            d["total"] = _to_decimal_or_none(d.get("total"))
            out.append(d)
        return out
