"""PTB-facing bot that wires services into handler callbacks."""

from __future__ import annotations

import logging

from app.ai.base import ReceiptVisionProvider
from app.bot import messages as msg
from app.bot.handlers import CommandHandlersMixin
from app.bot.job_processor import MAX_CAPTION_CHARS, JobProcessor, _clamp_caption
from app.bot.locks import UserLockManager
from app.bot.queue import JobQueue
from app.bot.receipt_input import ReceiptInputMixin
from app.bot.throttle import PasswordThrottle
from app.config import Config
from app.services.receipt_service import ProcessingService
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)

__all__ = ["ReimbursementBot", "MAX_CAPTION_CHARS", "_clamp_caption"]


class ReimbursementBot(CommandHandlersMixin, ReceiptInputMixin):
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


