"""PTB-facing bot that wires services into handler callbacks."""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.ai.base import ReceiptVisionProvider
from app.bot import messages as msg
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
from app.bot.queue import Job, JobQueue
from app.bot.states import BotState
from app.bot.throttle import PasswordThrottle
from app.config import Config
from app.services.receipt_service import (
    ProcessingError,
    ProcessingService,
    run_with_cleanup,
)
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService
from app.utils import files as file_utils
from app.utils import metrics
from app.utils.logging import configure_logging, request_scope

logger = logging.getLogger(__name__)


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
        self.queue = JobQueue(worker_count=config.worker_count)
        self.throttle = PasswordThrottle(
            max_attempts=config.password_max_attempts,
            lockout_seconds=config.password_lockout_seconds,
        )
        configure_logging(config.log_level, config.log_format)

    def start_workers(self) -> None:
        """Start the background workers that drain the job queue."""
        self.queue.start(self._process_job)

    async def stop_workers(self) -> None:
        """Cancel and await the background workers (shutdown)."""
        await self.queue.stop()

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
        await self.sessions.clear_receipts(user.id)  # atomic cross-process clear
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
            # Generation in flight or queued; reject input so it isn't misread
            # as password/heading.
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
            # Atomic cross-process append (avoids get->mutate->upsert lost append).
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
        position = self.queue.enqueue(
            Job(
                user_id=session.user_id,
                chat_id=session.chat_id,
                file_ids=list(session.receipt_file_ids),
                title=session.report_title,
            )
        )
        await self._reply(update, msg.QUEUED.format(position=position))

    async def _process_job(self, job: Job) -> None:
        """Process one queued job: acquire the per-user lock + lease, run the
        pipeline, deliver the PDF, then release everything.

        Runs in a background worker, so it messages the user via ``chat_id``
        rather than a PTB ``update``. The lease is always released in ``finally``
        so a notification failure can never leak it.
        """
        if not await self.locks.acquire(job.user_id):
            await self._notify(job.chat_id, msg.BUSY)
            return
        if not await self.sessions.try_acquire_processing(job.user_id):
            self.locks.release(job.user_id)
            await self._notify(job.chat_id, msg.BUSY)
            return
        request_id = uuid.uuid4().hex[:6]
        # Heartbeat keeps the lease alive past its TTL so a rival can't double-process.
        heartbeat = asyncio.get_running_loop().create_task(
            self._renew_lease_loop(job.user_id)
        )
        delivered = False
        session = None
        try:
            session = await self.sessions.get(job.user_id)
            session.state = BotState.PROCESSING
            await self.sessions.save(session)
            await self._notify(
                job.chat_id, msg.PROCESSING_STARTED.format(n=len(job.file_ids))
            )
            with request_scope(request_id):
                try:
                    async def deliver(result):
                        nonlocal delivered
                        caption = self._report_caption(result)
                        await self.telegram.send_document(
                            job.chat_id, result.out_pdf_path, caption=caption
                        )
                        await self.processing.mark_delivered(request_id)
                        metrics.inc("delivered")
                        delivered = True

                    async def on_progress(done, total):
                        await self._notify(
                            job.chat_id,
                            msg.PROCESSING_PROGRESS.format(done=done, total=total),
                        )

                    await run_with_cleanup(
                        self.processing,
                        job.user_id,
                        list(job.file_ids),
                        self.config.temp_dir,
                        deliver=deliver,
                        request_id=request_id,
                        title=job.title,
                        on_progress=on_progress,
                    )
                    session.state = BotState.IDLE
                except ProcessingError:
                    await self._notify(
                        job.chat_id,
                        msg.ERROR_MESSAGE.format(request_id=request_id or "unknown"),
                    )
                    session.state = BotState.IDLE
                except Exception:
                    logger.exception("unhandled processing error")
                    await self._notify(
                        job.chat_id,
                        msg.ERROR_MESSAGE.format(request_id=request_id or "unknown"),
                    )
                    session.state = BotState.IDLE
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            self.locks.release(job.user_id)
            await self.sessions.release_processing(job.user_id)
            if session is not None:
                session.state = BotState.IDLE
                if delivered:
                    # Clear staged receipts (atomic) only on confirmed delivery so a
                    # failed send can be retried with /generate without re-uploading.
                    session.report_title = ""
                    session.receipt_file_ids = []
                    await self.sessions.clear_receipts(job.user_id)
                await self.sessions.save(session)

    async def _notify(self, chat_id: int, text: str) -> None:
        """Best-effort text message to ``chat_id`` (worker context)."""
        if not text:
            return
        try:
            await self.telegram.send_message(chat_id, text)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("notify failed: %s", exc)

    async def _renew_lease_loop(self, user_id: int) -> None:
        """Heartbeat-renew the cross-process lease so a long run isn't reclaimed."""
        interval = self.config.session_lease_ttl_seconds / 3.0
        if interval <= 0:
            interval = 1.0
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.sessions.renew_processing_lease(user_id)
                if not renewed:
                    return  # lease already released -> nothing to renew
            except Exception:
                logger.exception("processing lease renewal failed")

    def _candidate_text(self, update) -> str:
        m = update.message
        if m and m.text:
            return m.text
        return ""  # a non-text message while awaiting password counts as wrong

    def _report_caption(self, result) -> str:
        totals = "\n".join(
            f"{c} Total: {_fmt(result.batch.currency_totals[c])}"
            for c in result.batch.currencies()
        )
        caption = msg.REPORT_READY.format(
            receipts=len(result.batch.receipts),
            processed=result.processed_count,
            review=result.review_count,
            totals=totals,
        )
        failures = getattr(result, "receipt_failures", None) or []
        if failures:
            reasons = "; ".join(f["reason"] for f in failures)
            caption += f"\n\nCould not process {len(failures)} receipt(s): {reasons}"
        return _clamp_caption(caption)


    async def _reply(self, update, text: str) -> None:
        if not text:
            return
        try:
            await update.effective_message.reply_text(text)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("reply failed: %s", exc)


def _fmt(value) -> str:
    from decimal import Decimal

    return f"{Decimal(value):,.2f}"


# Telegram media captions max 1024 chars; longer ones get a trailing ellipsis.
MAX_CAPTION_CHARS = 1024


def _clamp_caption(caption: str) -> str:
    if len(caption) <= MAX_CAPTION_CHARS:
        return caption
    return caption[: MAX_CAPTION_CHARS - 1].rstrip() + "…"
