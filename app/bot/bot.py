"""PTB-facing bot that wires services into handler callbacks."""

from __future__ import annotations

import logging

from app.ai.base import ReceiptVisionProvider
from app.bot import messages as msg
from app.bot.locks import UserLockManager
from app.bot.logic import (
    handle_cancel,
    handle_clear,
    handle_generate,
    handle_help,
    handle_password,
    handle_receipt,
    handle_start,
    handle_status,
)
from app.bot.states import BotState
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
from app.utils.logging import configure_logging

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
        configure_logging(config.log_level)

    # ---- guards -----------------------------------------------------------
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
        session = self.sessions.set_chat_id(user.id, update.effective_chat.id)
        state, reply = handle_start()
        session.state = state
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
        session = self.sessions.get(user.id)
        await self._reply(update, handle_status(session)[1])

    async def clear_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = self.sessions.get(user.id)
        state, reply = handle_clear(session)
        session.state = state
        await self._reply(update, reply)

    async def cancel_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = self.sessions.get(user.id)
        state, reply = handle_cancel(session)
        session.state = state
        await self._reply(update, reply)

    async def generate_command(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, chat = auth
        session = self.sessions.get(user.id)
        session.chat_id = chat.id
        processing = session.processing or self.locks.get(user.id).locked()
        state, reply = handle_generate(
            session, has_password=self.security.has_password, processing=processing
        )
        if reply:
            await self._reply(update, reply)
        if state is not None:
            session.state = state

    # ---- message handling ----------------------------------------------------
    async def message_handler(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, chat = auth
        session = self.sessions.get(user.id)
        session.chat_id = chat.id

        if session.state == BotState.AWAITING_PASSWORD:
            await self._password_attempt(update, session)
            return

        # Otherwise it's a receipt upload (photo or image document).
        file_id, mime, is_image = self._extract_file(update)
        if file_id is None:
            await self._reply(update, msg.UNSUPPORTED_DOCUMENT)
            return
        state, reply, should_add = handle_receipt(
            session,
            file_id,
            has_id=file_id in session.receipt_file_ids,
            max_receipts=self.config.max_receipts,
            processing=session.processing or bool(self.locks.get(user.id).locked()),
            awaiting_password=False,
        )
        if state is not None:
            session.state = state
        if should_add:
            session.add_file_id(file_id)
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

    # ---- password & processing --------------------------------------------
    async def _password_attempt(self, update, session) -> None:
        candidate = self._candidate_text(update)
        new_state, reply, correct = handle_password(session, candidate, security=self.security)
        # Best-effort delete the password message to avoid it lingering in chat.
        if update.effective_message:
            await self.telegram.delete_message(session.chat_id, update.effective_message.message_id)
        if not correct:
            session.state = new_state
            if reply:
                await self._reply(update, reply)
            return
        session.state = BotState.PROCESSING
        await self._reply(update, msg.PROCESSING_STARTED.format(n=len(session.receipt_file_ids)))
        await self._run_generation(update, session)

    async def _run_generation(self, update, session) -> None:
        acquired = await self.locks.acquire(session.user_id)
        if not acquired:
            await self._reply(update, msg.BUSY)
            session.state = BotState.IDLE
            return
        session.processing = True
        request_id = None
        try:
            async def deliver(result):
                nonlocal request_id
                request_id = result.request_id
                caption = self._report_caption(result)
                await self.telegram.send_document(
                    session.chat_id, result.out_pdf_path, caption=caption
                )

            await run_with_cleanup(
                self.processing,
                session.user_id,
                list(session.receipt_file_ids),
                self.config.temp_dir,
                deliver=deliver,
            )
            session.state = BotState.IDLE
        except ProcessingError:
            await self._reply(update, msg.ERROR_MESSAGE.format(request_id=request_id or "unknown"))
            session.state = BotState.IDLE
        except Exception:
            logger.exception("unhandled processing error")
            await self._reply(update, msg.ERROR_MESSAGE.format(request_id=request_id or "unknown"))
            session.state = BotState.IDLE
        finally:
            self.locks.release(session.user_id)
            session.processing = False
            session.clear_receipts()

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
        return msg.REPORT_READY.format(
            receipts=len(result.batch.receipts),
            processed=result.processed_count,
            review=result.review_count,
            totals=totals,
        )

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
