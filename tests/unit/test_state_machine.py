"""Tests for the conversation state machine."""

import pytest

from app.bot.states import BotState, StateMachineError, transition, valid_trigger


def test_idle_upload_to_collecting():
    assert transition(BotState.IDLE, "upload") == BotState.COLLECTING


def test_idle_generate_to_password():
    assert transition(BotState.IDLE, "generate") == BotState.AWAITING_PASSWORD


def test_collecting_upload_stays_collecting():
    assert transition(BotState.COLLECTING, "upload") == BotState.COLLECTING


def test_collecting_generate_to_password():
    assert transition(BotState.COLLECTING, "generate") == BotState.AWAITING_PASSWORD


def test_collecting_clear_to_idle():
    assert transition(BotState.COLLECTING, "clear") == BotState.IDLE


def test_password_correct_to_processing():
    assert transition(BotState.AWAITING_PASSWORD, "correct_password") == BotState.PROCESSING


def test_password_incorrect_to_idle():
    assert transition(BotState.AWAITING_PASSWORD, "incorrect_password") == BotState.IDLE


def test_password_cancel_to_idle():
    assert transition(BotState.AWAITING_PASSWORD, "cancel") == BotState.IDLE


def test_processing_success_to_idle():
    assert transition(BotState.PROCESSING, "success") == BotState.IDLE


def test_processing_failure_to_idle():
    assert transition(BotState.PROCESSING, "failure") == BotState.IDLE


@pytest.mark.parametrize("state", list(BotState))
def test_clear_only_valid_from_collecting(state):
    result = valid_trigger(state, "clear")
    assert result == (state == BotState.COLLECTING)


def test_invalid_transition_raises():
    with pytest.raises(StateMachineError):
        transition(BotState.IDLE, "clear")


def test_unknown_trigger_raises():
    with pytest.raises(StateMachineError):
        transition(BotState.IDLE, "bogus")


def test_invalid_state_value():
    with pytest.raises(ValueError):
        BotState("NOT_A_STATE")
    assert BotState("IDLE") == BotState.IDLE
