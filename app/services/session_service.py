"""In-memory per-user session store."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from app.models.session import Session


class SessionStore:
    """Thread/async-safe in-memory session registry keyed by user_id.

    No persistence — all state is lost on process restart, which is acceptable
    for V1 (receipts live in Telegram as file_ids anyway).
    """

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[int, Session] = {}
        self._lock = Lock()

    def get(self, user_id: int) -> Session:
        with self._lock:
            session = self._sessions.get(user_id)
            if session is None:
                session = Session(user_id=user_id, chat_id=user_id)
                self._sessions[user_id] = session
            return session

    def set_chat_id(self, user_id: int, chat_id: int) -> Session:
        with self._lock:
            session = self.get_locked(user_id)
            session.chat_id = chat_id
            session.touch()
            return session

    def get_locked(self, user_id: int) -> Session:
        """Return the session, creating it if absent. Caller must hold lock."""
        session = self._sessions.get(user_id)
        if session is None:
            session = Session(user_id=user_id, chat_id=user_id)
            self._sessions[user_id] = session
        return session

    def clear(self, user_id: int) -> None:
        with self._lock:
            self._sessions.pop(user_id, None)

    def expire_stale(self, now: datetime | None = None) -> int:
        """Remove expired sessions, returning how many were removed."""
        now = now or datetime.now(timezone.utc)
        removed = 0
        with self._lock:
            for uid in list(self._sessions):
                if self._sessions[uid].is_expired(self._ttl, now=now):
                    del self._sessions[uid]
                    removed += 1
        return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
