"""PTB-facing bot that wires services into handler callbacks."""

from __future__ import annotations

import logging

from app.ai.base import ReceiptVisionProvider
from app.bot import messages as msg
from app.bot.job_processor import MAX_CAPTION_CHARS, JobProcessor, _clamp_caption
from app.bot.locks import UserLockManager
from app.bot.logic import (
    handle_cancel,
    handle_clear,
    handle_generate,
    handle_heading,
    handle_help,
    handle_password,
    handle_receipt,
    handle_start,
    handle_status,
)
from app.bot.queue import Job, JobQueue, QueueFullError
from app.bot.states import BotState
from app.bot.throttle import PasswordThrottle
from app.config import Config
from app.services.receipt_service import ProcessingService
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService
from app.utils import files as file_utils
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)

__all__ = ["ReimbursementBot", "MAX_CAPTION_CHARS", "_clamp_caption"]


class ReimbursementBot:
    """Holds dependencies and exposes PTB-compatible handler callables."""

    def __init__(
        self,
        config: Config,
        security: SecurityService,
        sessions: SessionStore,
        telegram: TelegramService,
        provider: ReceiptVisionProvider,
        processing: ProcessingService,
    ) -> None:
        self.config = config
        self.security = security
        self.sessions = sessions
        self.telegram = telegram
        self.processing = processing
        self.locks = UserLockManager()
        self.queue = JobQueue(
            worker_count=config.worker_count, max_queue_size=config.max_queue_size
        )
        self.throttle = PasswordThrottle(
            max_attempts=config.password_max_attempts,
            lockout_seconds=config.password_lockout_seconds,
        )
        self.job_processor = JobProcessor(
            locks=self.locks,
            sessions=self.sessions,
            telegram=self.telegram,
            processing=self.processing,
            config=self.config,
        )
        configure_logging(config.log_level, config.log_format)

    def start_workers(self) -> None:
        """Start the background workers that drain the job queue."""
        self.queue.start(self.job_processor.process)

    async def stop_workers(self) -> None:
        """Cancel and await the background workers (shutdown)."""
        await self.queue.stop()

    async def notify_queued_lost(self) -> int:
        """Notify users whose queued or in-flight jobs were lost (in-memory
        queue reset on restart) and reset those sessions to IDLE so they can
        re-run /generate.

        Returns how many users were notified. Best-effort per user: a send
        failure is logged and does not stop the reset.
        """
        stale = await self.sessions.get_stale()
        if not stale:
            return 0
        for user_id, chat_id in stale:
            try:
                await self.telegram.send_message(chat_id, msg.QUEUE_LOST)
            except Exception:
                logger.exception(
                    "failed to notify user %s of lost queued job", user_id
                )
        await self.sessions.reset_stale()
        return len(stale)

    def _authorized(self, update):
        user = update.effective_user
        chat = update.effective_chat
        if not self.security.is_authorized(user.id if user else None, chat.id if chat else None):
            return None
        return user, chat

    async def start_command(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = auth
        await self.sessions.set_chat_id(user.id, update.effective_chat.id)
        state, reply = handle_start()
        await self.sessions.set_state(user.id, state)
        await self._reply(update, reply)

    async def help_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        await self._reply(update, handle_help()[1])

    async def status_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
        await self._reply(update, handle_status(session)[1])

    async def clear_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
        if session.state in (BotState.PROCESSING, BotState.QUEUED):
            await self._reply(update, msg.BUSY)
            return
        state, reply = handle_clear(session)  # clears receipts on the detached copy
        session.state = state
        session.report_title = ""
        await self.sessions.clear_receipts(user.id)  # atomic SQL clear
        await self.sessions.save(session)
        await self._reply(update, reply)

    async def cancel_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
        if session.state in (BotState.PROCESSING, BotState.QUEUED):
            await self._reply(update, msg.BUSY)
            return
        state, reply = handle_cancel(session)
        session.state = state
        await self.sessions.save(session)
        await self._reply(update, reply)

    async def generate_command(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, chat = auth
        session = await self.sessions.get(user.id)
        session.chat_id = chat.id
        processing = (
            await self.sessions.is_processing(user.id)
            or bool(self.locks.get(user.id).locked())
            or session.state == BotState.QUEUED
        )
        state, reply = handle_generate(
            session, has_password=self.security.has_password, processing=processing
        )
        if state is not None:
            session.state = state
        await self.sessions.save(session)
        if reply:
            await self._reply(update, reply)

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

    async def _reply(self, update, text: str) -> None:
        if not text:
            return
        try:
            await update.effective_message.reply_text(text)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("reply failed: %s", exc)
