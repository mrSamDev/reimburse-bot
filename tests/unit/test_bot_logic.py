"""Tests for the pure bot decision logic."""

from app.bot import logic, messages as msg
from app.bot.states import BotState
from app.config import Config
from app.models.session import Session
from app.services.security_service import SecurityService


def _sec(password="secret"):
    return SecurityService(Config(bot_password=password))


def _session_with(files):
    s = Session(user_id=1, chat_id=1, state=BotState.COLLECTING)
    for f in files:
        s.add_file_id(f)
    return s


def test_start():
    state, reply = logic.handle_start()
    assert state == BotState.IDLE
    assert "Ready" in reply


def test_help():
    assert "/generate" in logic.handle_help()[1]


def test_status_shows_count():
    s = _session_with(["f1", "f2", "f3"])
    assert logic.handle_status(s)[1] == "Receipts staged: 3"


def test_clear_resets_session():
    s = _session_with(["f1"])
    state, reply = logic.handle_clear(s)
    assert state == BotState.IDLE
    assert s.receipt_file_ids == []
    assert "cleared" in reply.lower()


def test_cancel_from_awaiting_password():
    s = _session_with([])
    s.state = BotState.AWAITING_PASSWORD
    state, reply = logic.handle_cancel(s)
    assert state == BotState.IDLE


def test_generate_no_receipts():
    s = _session_with([])
    state, reply = logic.handle_generate(s, has_password=True, processing=False)
    assert state == s.state
    assert reply == msg.NO_RECEIPTS


def test_generate_no_password_configured():
    s = _session_with(["f1"])
    state, reply = logic.handle_generate(s, has_password=False, processing=False)
    assert reply == msg.NO_PASSWORD_CONFIGURED


def test_generate_prompts_password():
    s = _session_with(["f1"])
    state, reply = logic.handle_generate(s, has_password=True, processing=False)
    assert state == BotState.AWAITING_PASSWORD
    assert reply == msg.PASSWORD_PROMPT


def test_generate_busy():
    s = _session_with(["f1"])
    state, reply = logic.handle_generate(s, has_password=True, processing=True)
    assert state == s.state
    assert reply == msg.BUSY


def test_password_correct():
    s = Session(user_id=1, chat_id=1, state=BotState.AWAITING_PASSWORD)
    state, reply, correct = logic.handle_password(s, "secret", security=_sec())
    assert correct is True
    assert state == BotState.PROCESSING
    assert reply is None


def test_password_wrong():
    s = Session(user_id=1, chat_id=1, state=BotState.AWAITING_PASSWORD)
    state, reply, correct = logic.handle_password(s, "nope", security=_sec())
    assert correct is False
    assert state == BotState.IDLE
    assert reply == msg.WRONG_PASSWORD


def test_receipt_staged():
    s = _session_with([])
    state, reply, should_add = logic.handle_receipt(
        s, "f1", has_id=False, max_receipts=20, processing=False, awaiting_password=False
    )
    assert state == BotState.COLLECTING
    assert should_add is True
    assert "1/20" in reply


def test_receipt_duplicate():
    s = _session_with(["f1"])
    state, reply, should_add = logic.handle_receipt(
        s, "f1", has_id=True, max_receipts=20, processing=False, awaiting_password=False
    )
    assert should_add is False
    assert reply == msg.DUPLICATE_RECEIPT


def test_receipt_max_reached():
    s = _session_with(list(range(20)))
    state, reply, should_add = logic.handle_receipt(
        s, "f21", has_id=False, max_receipts=20, processing=False, awaiting_password=False
    )
    assert should_add is False
    assert "Maximum" in reply


def test_receipt_during_processing():
    s = _session_with(["f1"])
    state, reply, should_add = logic.handle_receipt(
        s, "f2", has_id=False, max_receipts=20, processing=True, awaiting_password=False
    )
    assert should_add is False
    assert "Please wait" in reply
