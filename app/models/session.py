"""In-memory session model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from app.bot.states import BotState


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Session(BaseModel):
    """Per-user transient conversation session.

    Stores only Telegram ``file_id`` values, never the receipt images
    themselves. Persisted to SQLite via :class:`SessionStore` (repository-style:
    mutate a detached snapshot, then ``save()``).

    ``receipt_file_ids`` is a **read-only property**: direct assignment raises
    ``AttributeError`` so a caller can't silently lose a list change through
    ``save()`` (which deliberately doesn't persist the list to avoid clobbering
    concurrent atomic appends). Use ``add_file_id()`` / ``clear_receipts()`` on
    the model, or the store's atomic SQL methods (``add_file_id`` /
    ``clear_receipts``) to modify the list.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: int
    chat_id: int
    state: BotState = BotState.IDLE
    report_title: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    _receipt_file_ids: list[str] = PrivateAttr(default_factory=list)

    def __init__(self, **data: Any) -> None:
        # Accept ``receipt_file_ids`` as a constructor kwarg and route it to the
        # private attribute (the public property has no setter).
        file_ids = data.pop("receipt_file_ids", None)
        super().__init__(**data)
        if file_ids is not None:
            self._receipt_file_ids = list(file_ids)

    @property
    def receipt_file_ids(self) -> list[str]:
        return self._receipt_file_ids

    @receipt_file_ids.setter
    def receipt_file_ids(self, _value: list[str]) -> None:
        raise AttributeError(
            "receipt_file_ids is read-only; use Session.add_file_id() "
            "or Session.clear_receipts() to modify it"
        )

    def touch(self) -> None:
        self.updated_at = _now()

    def add_file_id(self, file_id: str) -> bool:
        """Add a file id, returning True if it was newly added (not a dup)."""
        if file_id in self._receipt_file_ids:
            return False
        self._receipt_file_ids.append(file_id)
        self.touch()
        return True

    def clear_receipts(self) -> None:
        self._receipt_file_ids.clear()
        self.report_title = ""
        self.touch()

    def is_expired(self, ttl_seconds: int, *, now: datetime | None = None) -> bool:
        now = now or _now()
        age = (now - self.updated_at).total_seconds()
        return age > ttl_seconds
