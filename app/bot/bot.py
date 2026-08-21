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
        self.throttle = PasswordThrottle(
            max_attempts=config.password_max_attempts,
            lockout_seconds=config.password_lockout_seconds,
        )
        configure_logging(config.log_level, config.log_format)

    def _authorized(self, update):
        user = update.effective_user
        chat = update.effective_chat
        if not self.security.is_authorized(user.id if user else None, chat.id if chat else None):
            return None
        return user, chat

    # ---- commands ---------------------------------------------------------
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
        state, reply = handle_clear(session)  # clears receipts on the detached copy
        session.state = state
        session.report_title = ""
        # Atomic cross-process clear of the staged list + title.
        await self.sessions.clear_receipts(user.id)
        await self.sessions.save(session)
        await self._reply(update, reply)

    async def cancel_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
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
        processing = await self.sessions.is_processing(user.id) or bool(self.locks.get(user.id).locked())
        state, reply = handle_generate(
            session, has_password=self.security.has_password, processing=processing
        )
        if state is not None:
            session.state = state
        await self.sessions.save(session)
        if reply:
            await self._reply(update, reply)

    # ---- message handling ----------------------------------------------------
    async def message_handler(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, chat = auth
        session = await self.sessions.get(user.id)
        session.chat_id = chat.id

        if session.state == BotState.PROCESSING:
            # A generation is in flight for this user. Reject any further input
            # now so it is never misread as a password/heading attempt, and so
            # no save() touches the row mid-generation.
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
            # Atomic cross-process append (never a get->mutate->upsert, which can
            # lose a concurrent append). Returns False only if another instance
            # already staged the same file_id meanwhile.
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
        # Photo: use the largest available size.
        if msg_obj.photo:
            photo = msg_obj.photo[-1]
            return photo.file_id, "image/jpeg", True
        if msg_obj.document:
            doc = msg_obj.document
            mime = doc.mime_type
            if file_utils.is_supported_mime(mime):
                return doc.file_id, mime, True
            return None, mime, False
        return None, None, False

    # ---- heading, password & processing -----------------------------------
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
        session.state = BotState.PROCESSING
        await self._reply(update, msg.PROCESSING_STARTED.format(n=len(session.receipt_file_ids)))
        await self._run_generation(update, session)

    async def _run_generation(self, update, session) -> None:
        # In-process fast path first, then an atomic cross-process DB claim.
        if not await self.locks.acquire(session.user_id):
            await self._reply(update, msg.BUSY)
            session.state = BotState.IDLE
            await self.sessions.save(session)
            return
        if not await self.sessions.try_acquire_processing(session.user_id):
            self.locks.release(session.user_id)
            await self._reply(update, msg.BUSY)
            session.state = BotState.IDLE
            await self.sessions.save(session)
            return
        # Persist PROCESSING before the long-running work so a concurrent message
        # routes to the busy guard above rather than being read as a password or
        # heading. save() no longer clobbers the processing lease.
        session.state = BotState.PROCESSING
        await self.sessions.save(session)
        # One id for the whole generation so every log line — including the
        # catch-all below — is attributable to the same request.
        request_id = uuid.uuid4().hex[:6]
        # Keep the cross-process lease alive for the whole run (it can exceed the
        # lease TTL, which would otherwise let a rival instance reclaim the slot
        # and double-process / double-bill). Cancelled in ``finally``.
        heartbeat = asyncio.get_running_loop().create_task(
            self._renew_lease_loop(session.user_id)
        )
        delivered = False
        try:
            with request_scope(request_id):
                try:
                    async def deliver(result):
                        nonlocal delivered
                        caption = self._report_caption(result)
                        await self.telegram.send_document(
                            session.chat_id, result.out_pdf_path, caption=caption
                        )
                        await self.processing.mark_delivered(request_id)
                        metrics.inc("delivered")
                        delivered = True

                    async def on_progress(done, total):
                        await self._reply(update, msg.PROCESSING_PROGRESS.format(done=done, total=total))

                    await run_with_cleanup(
                        self.processing,
                        session.user_id,
                        list(session.receipt_file_ids),
                        self.config.temp_dir,
                        deliver=deliver,
                        request_id=request_id,
                        title=session.report_title,
                        on_progress=on_progress,
                    )
                    session.state = BotState.IDLE
                except ProcessingError:
                    await self._reply(
                        update, msg.ERROR_MESSAGE.format(request_id=request_id or "unknown")
                    )
                    session.state = BotState.IDLE
                except Exception:
                    logger.exception("unhandled processing error")
                    await self._reply(
                        update, msg.ERROR_MESSAGE.format(request_id=request_id or "unknown")
                    )
                    session.state = BotState.IDLE
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            self.locks.release(session.user_id)
            await self.sessions.release_processing(session.user_id)
            session.state = BotState.IDLE
            if delivered:
                # Delivery confirmed: clear the staged receipts (atomic). If the
                # report could not be delivered (e.g. a transient Telegram send
                # failure), keep the receipts staged so the user can retry
                # /generate without re-uploading and re-staging everything.
                session.report_title = ""
                session.receipt_file_ids = []
                await self.sessions.clear_receipts(session.user_id)
            await self.sessions.save(session)

    async def _renew_lease_loop(self, user_id: int) -> None:
        """Heartbeat: refresh the cross-process processing lease during a long run.

        A generation that runs longer than the lease TTL would otherwise look
        crashed and be reclaimed by another instance mid-run (double extraction /
        double billing). Renewing on a fraction of the lease TTL keeps the slot
        held exactly as long as the work needs; the loop stops once the lease is
        released (clean shutdown) and dies with the process on a crash, so a
        dead run's lease still expires and becomes reclaimable.
        """
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


# Telegram media captions are limited to 1024 characters. Captions that exceed
# this (e.g. many failed-receipt reasons) are trimmed with a trailing ellipsis.
MAX_CAPTION_CHARS = 1024


def _clamp_caption(caption: str) -> str:
    if len(caption) <= MAX_CAPTION_CHARS:
        return caption
    return caption[: MAX_CAPTION_CHARS - 1].rstrip() + "…"
