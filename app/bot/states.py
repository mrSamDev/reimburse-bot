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


# Ordered transitions: (from_state, trigger) -> to_state
TRANSITIONS: dict[tuple[BotState, str], BotState] = {
    (BotState.IDLE, "upload"): BotState.COLLECTING,
    (BotState.IDLE, "generate"): BotState.AWAITING_PASSWORD,
    (BotState.COLLECTING, "upload"): BotState.COLLECTING,
    (BotState.COLLECTING, "generate"): BotState.AWAITING_PASSWORD,
    (BotState.COLLECTING, "clear"): BotState.IDLE,
    (BotState.AWAITING_PASSWORD, "correct_password"): BotState.PROCESSING,
    (BotState.AWAITING_PASSWORD, "incorrect_password"): BotState.IDLE,
    (BotState.AWAITING_PASSWORD, "cancel"): BotState.IDLE,
    (BotState.PROCESSING, "success"): BotState.IDLE,
    (BotState.PROCESSING, "failure"): BotState.IDLE,
}


class StateMachineError(Exception):
    """Raised on an invalid state transition."""


def transition(state: BotState, trigger: str) -> BotState:
    """Return the next state for ``state``+``trigger`` or raise."""
    key = (BotState(state), trigger)
    if key not in TRANSITIONS:
        raise StateMachineError(
            f"Invalid transition: {key[0].value} -> '{key[1]}'"
        )
    return TRANSITIONS[key]


def valid_trigger(state: BotState, trigger: str) -> bool:
    return (BotState(state), trigger) in TRANSITIONS
