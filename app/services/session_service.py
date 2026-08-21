"""SQLite-backed per-user session store (multi-process safe)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.bot.states import BotState
from app.models.session import Session

_SCHEMA_VERSION = 1

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
}

_COLUMNS = ("user_id", "chat_id", "state", "receipt_file_ids",
            "processing", "created_at", "updated_at")


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

    def __init__(self, db_path: str | Path, ttl_seconds: int = 1800) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._migrate()

    # ---- connection / schema -------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
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
            processing=bool(row["processing"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _session_to_row(self, session: Session) -> tuple:
        return (
            session.user_id,
            session.chat_id,
            session.state.value,
            json.dumps(session.receipt_file_ids),
            int(session.processing),
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
                "processing=excluded.processing, updated_at=excluded.updated_at",
                self._session_to_row(session),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- public API --------------------------------------------------------
    async def get(self, user_id: int) -> Session:
        session = self._load(user_id)
        if session is None:
            return Session(user_id=user_id, chat_id=user_id)
        return session

    async def save(self, session: Session) -> None:
        self._upsert(session)

    async def set_chat_id(self, user_id: int, chat_id: int) -> Session:
        session = await self.get(user_id)
        session.chat_id = chat_id
        session.touch()
        self._upsert(session)
        return session

    async def set_state(self, user_id: int, state: BotState) -> Session:
        session = await self.get(user_id)
        session.state = state
        session.touch()
        self._upsert(session)
        return session

    async def add_file_id(self, user_id: int, file_id: str) -> Session:
        session = await self.get(user_id)
        session.add_file_id(file_id)
        self._upsert(session)
        return session

    async def clear_receipts(self, user_id: int) -> Session:
        session = await self.get(user_id)
        session.clear_receipts()
        self._upsert(session)
        return session

    async def clear(self, user_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    async def purge_expired(self) -> int:
        """Remove sessions idle past the TTL; also clears stale processing flags."""
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

    async def try_acquire_processing(self, user_id: int) -> bool:
        """Atomically claim the per-user processing slot across processes.

        Returns True iff this caller won the claim (row was not already held).
        """
        conn = self._connect()
        try:
            # Ensure a row exists (create absent) without overwriting live state.
            conn.execute(
                f"INSERT OR IGNORE INTO sessions ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                (user_id, user_id, BotState.IDLE.value, json.dumps([]),
                 0, _utc_now(), _utc_now()),
            )
            cur = conn.execute(
                "UPDATE sessions SET processing = 1 "
                "WHERE user_id = ? AND processing = 0",
                (user_id,),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    async def release_processing(self, user_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET processing = 0 WHERE user_id = ?", (user_id,)
            )
            conn.commit()
        finally:
            conn.close()

    async def count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        finally:
            conn.close()
