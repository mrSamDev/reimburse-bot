"""Message input handling: receipt uploads, headings, and password attempts.

The security-sensitive password logic itself lives in the pure
:func:`handle_password` (``app/bot/logic.py``); this mixin is just the wiring
that connects a PTB update to it and to the throttle/queue.
"""

from __future__ import annotations

from app.bot import messages as msg
from app.bot.base import _BotBase
from app.bot.logic import handle_heading, handle_password, handle_receipt
from app.bot.queue import Job, QueueFullError
from app.bot.states import BotState
from app.utils import files as file_utils


class ReceiptInputMixin(_BotBase):
    """Handles message input: receipt uploads, headings, and passwords."""

    async def message_handler(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, chat = auth
        session = await self.sessions.get(user.id)
        session.chat_id = chat.id

        if session.state in (BotState.PROCESSING, BotState.QUEUED):
            # Reject input while a job is in flight or queued.
            await self._reply(update, msg.BUSY)
            return

        if session.state == BotState.AWAITING_PASSWORD:
            await self._password_attempt(update, session)
            return

        if session.state == BotState.AWAITING_HEADING:
            await self._heading_attempt(update, session)
            return

        # Otherwise it's a receipt upload (photo or image document).
        file_id, mime, is_image = self._extract_file(update)
        if file_id is None:
            await self.sessions.save(session)  # persist chat_id
            await self._reply(update, msg.UNSUPPORTED_DOCUMENT)
            return
        state, reply, should_add = handle_receipt(
            session,
            file_id,
            has_id=file_id in session.receipt_file_ids,
            max_receipts=self.config.max_receipts,
            processing=await self.sessions.is_processing(user.id) or bool(self.locks.get(user.id).locked()),
            awaiting_password=False,
        )
        if state is not None:
            session.state = state
        if should_add:
            # Atomic SQL append (avoids get->mutate->upsert lost append).
            if not await self.sessions.add_file_id(user.id, file_id):
                session.state = BotState.COLLECTING
                await self.sessions.save(session)
                await self._reply(update, msg.DUPLICATE_RECEIPT)
                return
            session.receipt_file_ids = session.receipt_file_ids + [file_id]
        await self.sessions.save(session)
        if reply:
            await self._reply(update, reply)

    def _extract_file(self, update) -> tuple[str | None, str | None, bool]:
        msg_obj = update.message
        if not msg_obj:
            return None, None, False
        if msg_obj.photo:
            photo = msg_obj.photo[-1]  # largest available size
            return photo.file_id, "image/jpeg", True
        if msg_obj.document:
            doc = msg_obj.document
            mime = doc.mime_type
            if file_utils.is_supported_mime(mime):
                return doc.file_id, mime, True
            return None, mime, False
        return None, None, False

    async def _heading_attempt(self, update, session) -> None:
        candidate = self._candidate_text(update)
        new_state, reply, valid = handle_heading(session, candidate)
        session.state = new_state
        await self.sessions.save(session)
        if reply:
            await self._reply(update, reply)

    async def _password_attempt(self, update, session) -> None:
        candidate = self._candidate_text(update)
        # Non-text input while awaiting password: ignore (don't consume/delete/cancel).
        if candidate == "":
            await self._reply(update, msg.PASSWORD_PROMPT)
            return
        if self.throttle.is_locked(session.user_id):
            await self._reply(update, msg.PASSWORD_LOCKED)
            return
        new_state, reply, correct = handle_password(session, candidate, security=self.security)
        if update.effective_message:
            await self.telegram.delete_message(session.chat_id, update.effective_message.message_id)  # don't leave password in chat
        if not correct:
            self.throttle.record_failure(session.user_id)
            session.state = new_state
            await self.sessions.save(session)
            if self.throttle.is_locked(session.user_id):
                await self._reply(update, msg.PASSWORD_LOCKED)
            else:
                remaining = self.throttle.remaining_attempts(session.user_id)
                await self._reply(
                    update, f"{reply} ({remaining} attempt{'s' if remaining != 1 else ''} remaining)"
                )
            return
        self.throttle.reset(session.user_id)
        session.state = new_state  # QUEUED: job is enqueued, worker processes it
        await self.sessions.save(session)
        try:
            position = self.queue.enqueue(
                Job(
                    user_id=session.user_id,
                    chat_id=session.chat_id,
                    file_ids=list(session.receipt_file_ids),
                    title=session.report_title,
                )
            )
        except QueueFullError:
            # Queue at capacity: revert to IDLE, keep receipts staged so the
            # user can retry /generate without re-uploading.
            session.state = BotState.IDLE
            await self.sessions.save(session)
            await self._reply(update, msg.QUEUE_FULL)
            return
        await self._reply(update, msg.QUEUED.format(position=position))

    def _candidate_text(self, update) -> str:
        m = update.message
        if m and m.text:
            return m.text
        return ""  # a non-text message while awaiting password counts as wrong
