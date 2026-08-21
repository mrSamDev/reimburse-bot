"""In-memory session model."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.bot.states import BotState


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Session(BaseModel):
    """Per-user transient conversation session.

    Stores only Telegram ``file_id`` values, never the receipt images
    themselves. Kept in memory; not persisted to a database in V1.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: int
    chat_id: int
    state: BotState = BotState.IDLE
    receipt_file_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    processing: bool = False

    def touch(self) -> None:
        self.updated_at = _now()

    def add_file_id(self, file_id: str) -> bool:
        """Add a file id, returning True if it was newly added (not a dup)."""
        if file_id in self.receipt_file_ids:
            return False
        self.receipt_file_ids.append(file_id)
        self.touch()
        return True

    def clear_receipts(self) -> None:
        self.receipt_file_ids = []
        self.touch()

    def is_expired(self, ttl_seconds: int, *, now: datetime | None = None) -> bool:
        now = now or _now()
        age = (now - self.updated_at).total_seconds()
        return age > ttl_seconds
