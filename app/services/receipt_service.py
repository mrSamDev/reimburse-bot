"""Orchestrates the full receipt processing pipeline."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.ai.base import AIProviderError, ReceiptExtraction, ReceiptVisionProvider
from app.ai.validation import AIValidationError, validate_extraction
from app.config import Config
from app.models.receipt import Batch, Receipt
from app.services import file_validation
from app.services.cleanup_service import cleanup_request_dir
from app.services.ledger_service import ReceiptLedger
from app.services.pdf_service import generate_report
from app.services.telegram_service import TelegramService
from app.utils import images
from app.utils.logging import request_scope

logger = logging.getLogger(__name__)

# Exceptions that mark a single receipt as failed but let the batch continue.
RECEIPT_FAILURE_EXCEPTIONS = (
    AIProviderError,
    AIValidationError,
    file_validation.FileValidationError,
)


class ProcessingError(Exception):
    """Raised when processing cannot produce any usable report."""


@dataclass
class ProcessingResult:
    batch: Batch
    out_pdf_path: Path
    request_id: str
    request_base: Path
    processed_count: int = 0
    failed_count: int = 0
    review_count: int = 0


@dataclass
class _ReceiptOutcome:
    receipt: Receipt | None = None
    failed: bool = False
    reason: str = ""


def make_request_base(temp_root: str | Path, request_id: str) -> Path:
    base = Path(temp_root) / f"request_{request_id}"
    (base / "input").mkdir(parents=True, exist_ok=True)
    (base / "normalized").mkdir(parents=True, exist_ok=True)
    (base / "output").mkdir(parents=True, exist_ok=True)
    return base


def _pdf_filename(request_id: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"reimbursement_{date}_{request_id}.pdf"


async def _extract_with_retry(
    provider: ReceiptVisionProvider,
    image_path: str | Path,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _rand: Callable[[], float] = random.random,
) -> ReceiptExtraction:
    """Extract a receipt, retrying transient provider failures with backoff.

    Only retries :class:`AIProviderError` (transport/parse failures that may be
    transient). Validation errors are not retried because the AI already
    returned data that failed hard checks. Backoff uses full jitter to avoid a
    thundering herd on a recovering provider. Raises after ``max_attempts``.
    ``_sleep``/``_random`` are injectable for deterministic tests.
    """
    for attempt in range(max_attempts):
        try:
            return await asyncio.to_thread(provider.extract_receipt, image_path)
        except AIProviderError:
            if attempt == max_attempts - 1:
                raise
            # Full jitter: sleep in [0, base_delay * (attempt+1)].
            delay = base_delay * (attempt + 1) * _rand()
            await _sleep(delay)
    raise AIProviderError("unreachable")  # pragma: no cover


class ProcessingService:
    """Downloads, validates, normalizes, extracts and reports receipts.

    A single failing receipt never destroys the batch; it increments
    ``failed_count`` and processing continues. Cleanup is the caller's
    responsibility (:func:`run_with_cleanup` guarantees it).
    """

    def __init__(
        self,
        config: Config,
        provider: ReceiptVisionProvider,
        telegram: TelegramService,
        ledger: ReceiptLedger | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._telegram = telegram
        self._ledger = ledger

    def _to_entry(self, receipt: Receipt, request_id: str, user_id: int) -> dict:
        """Map a validated receipt to an audit-ledger row."""
        return {
            "user_id": user_id,
            "file_id": receipt.source_file_id,
            "merchant_name": receipt.merchant_name,
            "transaction_date": receipt.transaction_date,
            "currency": receipt.currency,
            "total": receipt.total,
            "review_required": receipt.review_required,
            "status": "accepted",
            "request_id": request_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    async def process(
        self,
        user_id: int,
        file_ids: list[str],
        request_base: Path | None = None,
    ) -> ProcessingResult:
        if not file_ids:
            raise ProcessingError("No receipts to process")
        if len(file_ids) > self._config.max_receipts:
            raise ProcessingError(f"Too many receipts (max {self._config.max_receipts})")

        if request_base is not None:
            request_id = request_base.name.removeprefix("request_")
            base = Path(request_base)
        else:
            request_id = uuid.uuid4().hex[:6]
            base = make_request_base(self._config.temp_dir, request_id)
        input_dir = base / "input"
        normalized_dir = base / "normalized"
        output_dir = base / "output"

        batch = Batch()
        image_map: dict[str, str] = {}
        failed = 0

        with request_scope(request_id):
            logger.info(
                "processing started: user=%s receipts=%d", user_id, len(file_ids)
            )
            loop = asyncio.get_running_loop()
            budget = self._config.max_processing_seconds
            deadline = (loop.time() + budget) if budget > 0 else None

            def _check_deadline() -> None:
                if deadline is not None and loop.time() > deadline:
                    raise ProcessingError(
                        f"Processing time limit ({budget}s) exceeded"
                    )

            try:
                for idx, file_id in enumerate(file_ids):
                    _check_deadline()
                    outcome = await self._process_one(file_id, idx, input_dir, normalized_dir)
                    if outcome.failed:
                        failed += 1
                        logger.info("receipt failed: %s", outcome.reason)
                        continue
                    receipt = outcome.receipt
                    if receipt is None:
                        # Defensive: a successful outcome should always carry a receipt.
                        failed += 1
                        continue
                    batch.add(receipt)
                    image_map[receipt.source_file_id] = str(
                        normalized_dir / f"receipt_{idx:03d}.jpg"
                    )
                    if self._ledger is not None:
                        # Audit the accepted receipt immediately (idempotent by
                        # file_id) so a later PDF failure still leaves a trail.
                        await asyncio.to_thread(
                            self._ledger.insert,
                            self._to_entry(receipt, request_id, user_id),
                        )

                if not batch.receipts:
                    raise ProcessingError(
                        f"None of the {len(file_ids)} receipts could be processed"
                    )

                batch.processed_count = len(batch.receipts)
                batch.failed_count = failed
                batch.review_count = sum(1 for r in batch.receipts if r.review_required)

                _check_deadline()
                out_pdf = output_dir / _pdf_filename(request_id)
                await asyncio.to_thread(
                    generate_report,
                    batch,
                    out_pdf,
                    title=self._config.report_title,
                    period=self._config.report_period,
                    image_map=image_map,
                )
            except ProcessingError:
                raise
            except Exception as exc:
                logger.exception("processing failed (request %s)", request_id)
                raise ProcessingError(
                    "Something went wrong while processing your receipts"
                ) from exc

            return ProcessingResult(
                batch=batch,
                out_pdf_path=out_pdf,
                request_id=request_id,
                request_base=base,
                processed_count=len(batch.receipts),
                failed_count=failed,
                review_count=batch.review_count,
            )

    async def _process_one(
        self,
        file_id: str,
        idx: int,
        input_dir: Path,
        normalized_dir: Path,
    ) -> _ReceiptOutcome:
        raw_path = input_dir / f"receipt_{idx:03d}.img"
        norm_path = normalized_dir / f"receipt_{idx:03d}.jpg"
        try:
            await self._telegram.download_file(file_id, raw_path)
            file_validation.validate_downloaded_image(
                raw_path, max_size_mb=self._config.max_file_size_mb
            )
            await asyncio.to_thread(images.normalize_image, raw_path, norm_path)
            extraction = await _extract_with_retry(
                self._provider,
                norm_path,
                max_attempts=self._config.ai_retry_attempts,
                base_delay=self._config.ai_retry_base_delay,
            )
            receipt = validate_extraction(extraction, file_id)
            return _ReceiptOutcome(receipt=receipt)
        except RECEIPT_FAILURE_EXCEPTIONS as exc:
            return _ReceiptOutcome(failed=True, reason=str(exc))
        except Exception as exc:  # isolate this receipt, keep the batch alive
            logger.exception("unexpected error on receipt %s", file_id)
            return _ReceiptOutcome(failed=True, reason=f"unexpected: {exc}")


async def run_with_cleanup(
    service: ProcessingService,
    user_id: int,
    file_ids: list[str],
    temp_root: str | Path,
    deliver=None,
    request_id: str | None = None,
) -> ProcessingResult:
    """Run processing, deliver the report, then guarantee cleanup.

    ``deliver`` is an optional async callback ``deliver(result)`` invoked while
    the PDF still exists, so the bot can send it to Telegram before the request
    directory is removed. ``request_id`` lets a caller correlate logs emitted
    across the whole generation (including its own error handling) to one id.
    """
    request_id = request_id or uuid.uuid4().hex[:6]
    base = make_request_base(temp_root, request_id)
    try:
        result = await service.process(user_id, file_ids, request_base=base)
        if deliver is not None:
            await deliver(result)
        return result
    finally:
        cleanup_request_dir(base)
