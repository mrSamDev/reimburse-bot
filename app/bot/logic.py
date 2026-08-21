"""Pure bot decision logic, independent of python-telegram-bot.

Each function takes a session and configuration, applies the state machine and
returns ``(new_state, reply_text)`` (reply may be None). Handlers in
``commands.py``/``handlers.py`` translate these into PTB calls, keeping the core
testable without Telegram.
"""

from __future__ import annotations

from app.bot import messages as msg
from app.bot.states import BotState


def handle_start():
    return BotState.IDLE, msg.STARTED


def handle_help():
    return None, msg.HELP


def handle_status(session) -> tuple[None, str]:
    return None, msg.STATUS.format(n=len(session.receipt_file_ids))


def handle_clear(session) -> tuple:
    session.clear_receipts()
    return BotState.IDLE, msg.SESSION_CLEARED


def handle_cancel(session) -> tuple:
    # Only meaningful when awaiting the password; otherwise a no-op.
    return BotState.IDLE, msg.CANCELLED


def handle_generate(session, *, has_password: bool, processing: bool) -> tuple:
    if processing:
        return session.state, msg.BUSY
    if not session.receipt_file_ids:
        return session.state, msg.NO_RECEIPTS
    if not has_password:
        return session.state, msg.NO_PASSWORD_CONFIGURED
    return BotState.AWAITING_HEADING, msg.HEADING_PROMPT


def handle_heading(session, candidate: str) -> tuple:
    """Return (new_state, reply, valid_bool)."""
    title = (candidate or "").strip()
    if not title:
        return BotState.AWAITING_HEADING, msg.HEADING_EMPTY, False
    session.report_title = title
    return BotState.AWAITING_PASSWORD, msg.PASSWORD_PROMPT, True


def handle_password(session, candidate: str, *, security) -> tuple:
    """Return (new_state, reply, correct_bool)."""
    if security.check_password(candidate):
        return BotState.PROCESSING, None, True
    return BotState.IDLE, msg.WRONG_PASSWORD, False


def handle_receipt(
    session,
    file_id: str,
    *,
    has_id: bool,
    max_receipts: int,
    processing: bool,
    awaiting_password: bool,
) -> tuple:
    """Return (new_state, reply, should_add). Pure — does not mutate session."""
    if processing:
        return session.state, msg.UPLOAD_DURING_PROCESSING, False
    if awaiting_password:
        return session.state, None, False
    if len(session.receipt_file_ids) >= max_receipts:
        return session.state, msg.MAX_RECEIPTS_REACHED.format(max=max_receipts), False
    if has_id:
        return session.state, msg.DUPLICATE_RECEIPT, False
    reply = msg.RECEIPT_STORED.format(
        n=len(session.receipt_file_ids) + 1, max=max_receipts
    )
    return BotState.COLLECTING, reply, True
