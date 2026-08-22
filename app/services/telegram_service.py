"""Thin wrapper over the Telegram Bot API for download + delivery."""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.file_validation import OversizedFileError

logger = logging.getLogger(__name__)


class TelegramService:
    """Abstraction over a python-telegram-bot ``Bot``.

    The ``bot`` argument is the actual PTB ``Bot``/``Application`` in production
    and a fake in tests. Only ``get_file`` and ``send_document`` are used.
    """

    def __init__(self, bot, *, timeout: int = 30, max_file_size_mb: int = 10) -> None:
        self._bot = bot
        self._timeout = timeout
        self._max_bytes = max_file_size_mb * 1024 * 1024

    async def download_file(self, file_id: str, dest_path: str | Path) -> Path:
        """Download a Telegram file to ``dest_path``.

        The Telegram ``File`` object carries a server-reported size, so we check
        it *before* downloading to avoid buffering an oversized file.
        """
        file = await self._bot.get_file(file_id)
        size = getattr(file, "file_size", None)
        if size is not None and size > self._max_bytes:
            raise OversizedFileError(
                f"File exceeds {self._max_bytes // (1024*1024)} MB limit"
            )
        dest = Path(dest_path)
        await file.download_to_drive(
            dest, read_timeout=self._timeout, write_timeout=self._timeout
        )
        return dest

    async def send_document(self, chat_id: int, doc_path: str | Path, *, caption: str = "") -> None:
        with open(doc_path, "rb") as fh:
            await self._bot.send_document(
                chat_id,
                document=fh,
                caption=caption,
                read_timeout=self._timeout,
                write_timeout=self._timeout,
            )

    async def send_message(self, chat_id: int, text: str) -> None:
        """Send a plain text message to ``chat_id`` (used by background workers)."""
        await self._bot.send_message(chat_id, text)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self._bot.delete_message(chat_id, message_id)
        except Exception:  # best-effort deletion; never fail the flow
            logger.debug("could not delete message %s", message_id)
