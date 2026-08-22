"""Shared typed surface for the bot mixins.

``_BotBase`` declares the attributes and shared helpers that every mixin
needs. It has no ``__init__``: the concrete :class:`ReimbursementBot` assigns
all declared attributes in its own ``__init__``. Declaring them here lets mypy
type-check each mixin in isolation.
"""

from __future__ import annotations

import logging

from app.bot.locks import UserLockManager
from app.bot.queue import JobQueue
from app.bot.throttle import PasswordThrottle
from app.config import Config
from app.services.receipt_service import ProcessingService
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)


class _BotBase:
    """Shared attributes + reply/auth helpers for the PTB-facing mixins."""

    config: Config
    security: SecurityService
    sessions: SessionStore
    telegram: TelegramService
    processing: ProcessingService
    locks: UserLockManager
    queue: JobQueue
    throttle: PasswordThrottle

    async def _reply(self, update, text: str) -> None:
        if not text:
            return
        try:
            await update.effective_message.reply_text(text)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("reply failed: %s", exc)

    def _authorized(self, update):
        user = update.effective_user
        chat = update.effective_chat
        if not self.security.is_authorized(user.id if user else None, chat.id if chat else None):
            return None
        return user, chat
