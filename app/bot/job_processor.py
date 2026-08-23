"""Background worker that drains the queue, processing one job at a time.

Kept out of ``bot.py`` (the PTB-facing surface) so the worker's lock/state
release discipline can be reasoned about and reviewed in isolation. It runs in
a background task and messages the user via ``chat_id``, never via a PTB
``update``.
"""

from __future__ import annotations

import logging
import uuid

from app.bot import messages as msg
from app.bot.locks import UserLockManager
from app.bot.queue import Job
from app.bot.states import BotState
from app.config import Config
from app.services.receipt_service import (
    ProcessingError,
    ProcessingService,
    run_with_cleanup,
)
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService
from app.utils import metrics
from app.utils.logging import request_scope

logger = logging.getLogger(__name__)


class JobProcessor:
    """Runs the processing pipeline for queued jobs (``queue.start`` worker).

    Acquires the per-user lock and the processing slot before processing and
    always releases both in ``finally`` so a notification failure can never
    leak them.
    """

    def __init__(
        self,
        locks: UserLockManager,
        sessions: SessionStore,
        telegram: TelegramService,
        processing: ProcessingService,
        config: Config,
    ) -> None:
        self._locks = locks
        self._sessions = sessions
        self._telegram = telegram
        self._processing = processing
        self._config = config

    async def process(self, job: Job) -> None:
        """Process one queued job, deliver the PDF, then release everything."""
        if not await self._locks.acquire(job.user_id):
            await self._notify(job.chat_id, msg.BUSY)
            return
        if not await self._sessions.try_acquire_processing(job.user_id):
            self._locks.release(job.user_id)
            await self._notify(job.chat_id, msg.BUSY)
            return
        request_id = uuid.uuid4().hex[:6]
        delivered = False
        session = None
        try:
            session = await self._sessions.get(job.user_id)
            session.state = BotState.PROCESSING
            await self._sessions.save(session)
            await self._notify(
                job.chat_id, msg.PROCESSING_STARTED.format(n=len(job.file_ids))
            )
            with request_scope(request_id):
                try:
                    async def deliver(result):
                        nonlocal delivered
                        caption = self._report_caption(result)
                        await self._telegram.send_document(
                            job.chat_id, result.out_pdf_path, caption=caption
                        )
                        await self._processing.mark_delivered(request_id)
                        metrics.inc("delivered")
                        delivered = True

                    async def on_progress(done, total):
                        await self._notify(
                            job.chat_id,
                            msg.PROCESSING_PROGRESS.format(done=done, total=total),
                        )

                    await run_with_cleanup(
                        self._processing,
                        job.user_id,
                        list(job.file_ids),
                        self._config.temp_dir,
                        deliver=deliver,
                        request_id=request_id,
                        title=job.title,
                        on_progress=on_progress,
                    )
                except ProcessingError:
                    await self._notify(
                        job.chat_id,
                        msg.ERROR_MESSAGE.format(request_id=request_id or "unknown"),
                    )
                except Exception:
                    logger.exception("unhandled processing error")
                    await self._notify(
                        job.chat_id,
                        msg.ERROR_MESSAGE.format(request_id=request_id or "unknown"),
                    )
        finally:
            self._locks.release(job.user_id)
            await self._sessions.release_processing(job.user_id)
            if session is not None:
                # Single state-transition point: every outcome resets to IDLE.
                session.state = BotState.IDLE
                if delivered:
                    # Clear staged receipts only on confirmed delivery so a failed
                    # send is retryable without re-uploading (crash window may dup).
                    session.report_title = ""
                    session.receipt_file_ids = []
                    await self._sessions.clear_receipts(job.user_id)
                await self._sessions.save(session)

    async def _notify(self, chat_id: int, text: str) -> None:
        """Best-effort text message to ``chat_id`` (worker context)."""
        if not text:
            return
        try:
            await self._telegram.send_message(chat_id, text)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("notify failed: %s", exc)

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


def _fmt(value) -> str:
    from decimal import Decimal

    return f"{Decimal(value):,.2f}"


# Telegram media captions max 1024 chars; longer ones get a trailing ellipsis.
MAX_CAPTION_CHARS = 1024


def _clamp_caption(caption: str) -> str:
    if len(caption) <= MAX_CAPTION_CHARS:
        return caption
    return caption[: MAX_CAPTION_CHARS - 1].rstrip() + "…"
