"""Bot state machine definition."""

from __future__ import annotations

from enum import Enum


class BotState(str, Enum):
    """Conversation states of the bot."""

    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    AWAITING_PASSWORD = "AWAITING_PASSWORD"
    PROCESSING = "PROCESSING"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value
