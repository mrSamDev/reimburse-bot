"""SQLite-backed per-user session store (multi-process safe)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.bot.states import BotState
from app.models.session import Session
from app.services.backup_service import backup_database

_SCHEMA_VERSION = 3

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        state TEXT NOT NULL,
        receipt_file_ids TEXT NOT NULL,
        processing INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    2: """
    ALTER TABLE sessions ADD COLUMN lease_expiry TEXT;
    """,
    3: """
    ALTER TABLE sessions ADD COLUMN report_title TEXT NOT NULL DEFAULT '';
    """,
}

_COLUMNS = ("user_id", "chat_id", "state", "receipt_file_ids",
            "report_title", "created_at", "updated_at")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Durable, per-user session registry backed by SQLite (WAL).

    Persistence is repository-style: :meth:`get` returns a detached ``Session``
    snapshot; callers mutate it and persist via :meth:`save` (an upsert). This is
    correct across processes — no in-memory dict to diverge.

    Generation is serialized per user with :meth:`try_acquire_processing`, an
    atomic ``UPDATE ... WHERE processing = 0`` that is safe across instances.
    """

    def __init__(
        self,
        db_path: str | Path,
        ttl_seconds: int = 1800,
        lease_ttl_seconds: int = 120,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._lease_ttl = lease_ttl_seconds
        self._migrate()

    # ---- connection / schema -------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        # Explicit busy timeout (10s): a write must wait for the lock rather than
        # fail with "database is locked" under concurrent instances.
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
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

    # ---- row <-> Session --------------------------------------------------
    def _row_to_session(self, row: sqlite3.Row) -> Session:
        return Session(
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            state=BotState(row["state"]),
            receipt_file_ids=json.loads(row["receipt_file_ids"]),
            report_title=row["report_title"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _session_to_row(self, session: Session) -> tuple:
        return (
            session.user_id,
            session.chat_id,
            session.state.value,
            json.dumps(session.receipt_file_ids),
            session.report_title,
            session.created_at.isoformat(),
            session.updated_at.isoformat(),
        )

    def _load(self, user_id: int) -> Session | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                return None
            session = self._row_to_session(row)
            if session.is_expired(self._ttl):
                return None  # expired on read -> treated as absent
            return session
        finally:
            conn.close()

    def _upsert(self, session: Session) -> None:
        conn = self._connect()
        try:
            conn.execute(
                f"INSERT INTO sessions ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)}) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "chat_id=excluded.chat_id, state=excluded.state, "
                "receipt_file_ids=excluded.receipt_file_ids, "
                "report_title=excluded.report_title, "
                "updated_at=excluded.updated_at",
                self._session_to_row(session),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- public API --------------------------------------------------------
    # ---- public API (async wrappers over sync DB ops, off the event loop) ---
    def _op_get(self, user_id: int) -> Session:
        session = self._load(user_id)
        if session is None:
            return Session(user_id=user_id, chat_id=user_id)
        return session

    async def get(self, user_id: int) -> Session:
        return await asyncio.to_thread(self._op_get, user_id)

    async def save(self, session: Session) -> None:
        await asyncio.to_thread(self._upsert, session)

    def _op_set_chat_id(self, user_id: int, chat_id: int) -> Session:
        session = self._op_get(user_id)
        session.chat_id = chat_id
        session.touch()
        self._upsert(session)
        return session

    async def set_chat_id(self, user_id: int, chat_id: int) -> Session:
        return await asyncio.to_thread(self._op_set_chat_id, user_id, chat_id)

    def _op_set_state(self, user_id: int, state: BotState) -> Session:
        session = self._op_get(user_id)
        session.state = state
        session.touch()
        self._upsert(session)
        return session

    async def set_state(self, user_id: int, state: BotState) -> Session:
        return await asyncio.to_thread(self._op_set_state, user_id, state)

    def _op_add_file_id(self, user_id: int, file_id: str) -> Session:
        session = self._op_get(user_id)
        session.add_file_id(file_id)
        self._upsert(session)
        return session

    async def add_file_id(self, user_id: int, file_id: str) -> Session:
        return await asyncio.to_thread(self._op_add_file_id, user_id, file_id)

    def _op_clear_receipts(self, user_id: int) -> Session:
        session = self._op_get(user_id)
        session.clear_receipts()
        self._upsert(session)
        return session

    async def clear_receipts(self, user_id: int) -> Session:
        return await asyncio.to_thread(self._op_clear_receipts, user_id)

    def _op_clear(self, user_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    async def clear(self, user_id: int) -> None:
        await asyncio.to_thread(self._op_clear, user_id)

    def _op_purge_expired(self) -> int:
        """Remove sessions idle past the TTL (sync)."""
        now = datetime.now(timezone.utc)
        removed = 0
        conn = self._connect()
        try:
            for row in conn.execute("SELECT user_id, updated_at FROM sessions").fetchall():
                updated = datetime.fromisoformat(row["updated_at"])
                if (now - updated).total_seconds() > self._ttl:
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
                    removed += 1
            conn.commit()
        finally:
            conn.close()
        return removed

    async def purge_expired(self) -> int:
        return await asyncio.to_thread(self._op_purge_expired)

    def _op_sweep(self) -> dict[str, int]:
        """Reclaim abandoned leases and purge expired sessions (sync)."""
        conn = self._connect()
        now_iso = _utc_now()
        try:
            reclaimed = self._reclaim_expired_leases(conn, now_iso)
            conn.commit()
        finally:
            conn.close()
        purged = self._op_purge_expired()
        return {"reclaimed": reclaimed, "purged": purged}

    async def sweep(self) -> dict[str, int]:
        return await asyncio.to_thread(self._op_sweep)

    def _reclaim_expired_leases(
        self, conn: sqlite3.Connection, now_iso: str, user_id: int | None = None
    ) -> int:
        """Reset any abandoned (already-expired) processing lease."""
        if user_id is None:
            cur = conn.execute(
                "UPDATE sessions SET processing = 0, lease_expiry = NULL "
                "WHERE processing = 1 AND lease_expiry IS NOT NULL AND lease_expiry < ?",
                (now_iso,),
            )
        else:
            cur = conn.execute(
                "UPDATE sessions SET processing = 0, lease_expiry = NULL "
                "WHERE user_id = ? AND processing = 1 "
                "AND lease_expiry IS NOT NULL AND lease_expiry < ?",
                (user_id, now_iso),
            )
        return cur.rowcount

    def _op_try_acquire_processing(self, user_id: int) -> bool:
        """Atomically claim the per-user processing slot (sync)."""
        lease = _utc_now()
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=self._lease_ttl)).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO sessions ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                (user_id, user_id, BotState.IDLE.value, json.dumps([]), "", lease, lease),
            )
            self._reclaim_expired_leases(conn, lease, user_id)
            cur = conn.execute(
                "UPDATE sessions SET processing = 1, lease_expiry = ? "
                "WHERE user_id = ? AND processing = 0",
                (expiry, user_id),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    async def try_acquire_processing(self, user_id: int) -> bool:
        return await asyncio.to_thread(self._op_try_acquire_processing, user_id)

    def _op_release_processing(self, user_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET processing = 0, lease_expiry = NULL WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        finally:
            conn.close()

    async def release_processing(self, user_id: int) -> None:
        await asyncio.to_thread(self._op_release_processing, user_id)

    def _op_renew_processing_lease(self, user_id: int) -> bool:
        """Refresh a live processing lease's expiry (heartbeat).

        A generation that runs longer than ``lease_ttl_seconds`` would otherwise
        look crashed and be reclaimed by another instance mid-run (double
        extraction / double billing). Renewing the expiry as the work proceeds
        keeps the slot held for exactly as long as the run needs, while still
        releasing it promptly when the run ends or the process dies (the
        heartbeat dies with it). Returns False when there is nothing to renew
        (lease already released), so a heartbeat loop can stop cleanly.
        """
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=self._lease_ttl)).isoformat()
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE sessions SET lease_expiry = ? "
                "WHERE user_id = ? AND processing = 1",
                (expiry, user_id),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    async def renew_processing_lease(self, user_id: int) -> bool:
        return await asyncio.to_thread(self._op_renew_processing_lease, user_id)

    def _op_is_processing(self, user_id: int) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT processing FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
            return bool(row and row["processing"])
        finally:
            conn.close()

    async def is_processing(self, user_id: int) -> bool:
        return await asyncio.to_thread(self._op_is_processing, user_id)

    def backup(self, target_dir: str | Path, *, retention: int | None = None) -> Path:
        """Write a durable copy of the sessions DB into ``target_dir``."""
        return backup_database(self._db_path, target_dir, label="sessions", retention=retention)

    def _op_count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        finally:
            conn.close()

    async def count(self) -> int:
        return await asyncio.to_thread(self._op_count)

