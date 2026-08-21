"""Tests for the conversation state enum."""

import pytest

from app.bot.states import BotState


def test_invalid_state_value():
    with pytest.raises(ValueError):
        BotState("NOT_A_STATE")


def test_valid_state_value():
    assert BotState("IDLE") == BotState.IDLE


@pytest.mark.parametrize("state", list(BotState))
def test_all_enum_members_are_strings(state):
    # Handlers store state on sessions and compare by value; must be string-like.
    assert str(state) == state.value
