"""Orchestrates the full receipt processing pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from app.ai.base import AIProviderError, ReceiptVisionProvider
from app.ai.validation import AIValidationError, validate_extraction
from app.config import Config
from app.models.receipt import Batch, Receipt
from app.services import file_validation
from app.services.ledger_service import ReceiptLedger
from app.services.pdf_service import generate_report
from app.services.report_period import derive_report_period
from app.services.telegram_service import TelegramService
from app.utils import images, metrics
from app.utils.logging import request_scope

from .retry import _CallBudget, _extract_with_retry
from .types import (
    BudgetExceededError,
    ProcessingError,
    ProcessingResult,
    _pdf_filename,
    _ReceiptOutcome,
    make_request_base,
)

# Logged under the canonical package name so caplog/handler tests that target
# ``app.services.receipt_service`` keep capturing pipeline records unchanged.
logger = logging.getLogger("app.services.receipt_service")

# Exceptions that mark a single receipt as failed but let the batch continue.
RECEIPT_FAILURE_EXCEPTIONS = (
    AIProviderError,
    AIValidationError,
    file_validation.FileValidationError,
)


class ProcessingService:
    """Downloads, validates, normalizes, extracts and reports receipts.

    A single failing receipt never destroys the batch; it increments
    ``failed_count`` and processing continues.
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

    async def mark_delivered(self, request_id: str) -> None:
        """Record that a generated report was actually sent to the user."""
        if self._ledger is not None:
            await asyncio.to_thread(self._ledger.mark_delivered, request_id)

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
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
        *,  # keyword-only: positional callers keep working unchanged
        title: str = "",
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
        receipt_failures: list[dict] = []

        with request_scope(request_id):
            logger.info(
                "processing started: user=%s receipts=%d", user_id, len(file_ids)
            )
            batch_start = time.monotonic()
            loop = asyncio.get_running_loop()
            budget = self._config.max_processing_seconds
            deadline = (loop.time() + budget) if budget > 0 else None
            call_budget = _CallBudget(max_calls=self._config.ai_max_calls_per_run)

            def _check_deadline() -> None:
                # Soft total-time check: after the concurrent phase, before PDF.
                if deadline is not None and loop.time() > deadline:
                    raise ProcessingError(
                        f"Processing time limit ({budget}s) exceeded"
                    )

            try:
                # Phase 1: extract all receipts concurrently, semaphore-capped.
                sem = asyncio.Semaphore(self._config.ai_concurrency)

                async def _limited(file_id, idx):
                    async with sem:
                        result = await self._process_one(
                            file_id, idx, input_dir, normalized_dir, call_budget
                        )
                        # Space requests; holding the semaphore during sleep makes the gap real.
                        if idx < len(file_ids) - 1:
                            delay = self._config.ai_request_delay_seconds
                            if delay > 0:
                                await asyncio.sleep(delay)
                        return result

                gather = asyncio.gather(
                    *(_limited(file_id, idx) for idx, file_id in enumerate(file_ids))
                )
                if budget > 0:
                    # Hard cap: abort an over-budget batch rather than drain it.
                    try:
                        outcomes = await asyncio.wait_for(gather, timeout=budget)
                    except (asyncio.TimeoutError, TimeoutError) as exc:
                        raise ProcessingError(
                            f"Processing time limit ({budget}s) exceeded"
                        ) from exc
                    except BudgetExceededError as exc:
                        raise ProcessingError(str(exc)) from exc
                else:
                    outcomes = await gather

                # Phase 2: assemble the batch in input order (deterministic).
                for idx, (file_id, outcome) in enumerate(zip(file_ids, outcomes, strict=True)):
                    _check_deadline()
                    if outcome.failed:
                        failed += 1
                        metrics.inc("failed")
                        receipt_failures.append({"file_id": file_id, "reason": outcome.reason})
                        logger.info("receipt failed: %s", outcome.reason)
                        if self._ledger is not None:
                            await asyncio.to_thread(
                                self._ledger.insert_failure,
                                file_id,
                                outcome.reason,
                                request_id=request_id,
                                user_id=user_id,
                            )
                    else:
                        receipt = outcome.receipt
                        if receipt is None:
                            # A successful outcome should always carry a receipt.
                            failed += 1
                            metrics.inc("failed")
                        else:
                            batch.add(receipt)
                            metrics.inc("processed")
                            if receipt.review_required:
                                metrics.inc("review")
                            image_map[receipt.source_file_id] = str(
                                normalized_dir / f"receipt_{idx:03d}.jpg"
                            )
                            if self._ledger is not None:
                                # Audit the accepted receipt now (idempotent by file_id).
                                await asyncio.to_thread(
                                    self._ledger.insert,
                                    self._to_entry(receipt, request_id, user_id),
                                )
                    if on_progress is not None:
                        await on_progress(idx + 1, len(file_ids))

                if not batch.receipts:
                    raise ProcessingError(
                        f"None of the {len(file_ids)} receipts could be processed"
                    )

                batch.processed_count = len(batch.receipts)
                batch.failed_count = failed
                batch.review_count = sum(1 for r in batch.receipts if r.review_required)

                _check_deadline()
                out_pdf = output_dir / _pdf_filename(request_id)
                period = derive_report_period(batch.receipts)
                if not period:
                    logger.warning(
                        "could not derive report period from receipt dates; "
                        "omitting the report subtitle (request %s)",
                        request_id,
                    )
                await asyncio.to_thread(
                    generate_report,
                    batch,
                    out_pdf,
                    title=title or self._config.report_title,
                    period=period,
                    image_map=image_map,
                )
            except ProcessingError:
                raise
            except Exception as exc:
                logger.exception("processing failed (request %s)", request_id)
                raise ProcessingError(
                    "Something went wrong while processing your receipts"
                ) from exc

            metrics.observe("batch_processing_seconds", time.monotonic() - batch_start)
            return ProcessingResult(
                batch=batch,
                out_pdf_path=out_pdf,
                request_id=request_id,
                request_base=base,
                processed_count=len(batch.receipts),
                failed_count=failed,
                review_count=batch.review_count,
                receipt_failures=receipt_failures,
            )

    async def _process_one(
        self,
        file_id: str,
        idx: int,
        input_dir: Path,
        normalized_dir: Path,
        call_budget: _CallBudget | None = None,
    ) -> _ReceiptOutcome:
        raw_path = input_dir / f"receipt_{idx:03d}.img"
        norm_path = normalized_dir / f"receipt_{idx:03d}.jpg"
        timeout = self._config.ai_per_receipt_timeout_seconds
        # Monotonic deadline shared by retry capping and the hard ``wait_for`` backstop.
        deadline = asyncio.get_running_loop().time() + timeout
        start = time.monotonic()
        try:
            async def _work():
                await self._telegram.download_file(file_id, raw_path)
                file_validation.validate_downloaded_image(
                    raw_path, max_size_mb=self._config.max_file_size_mb
                )
                await asyncio.to_thread(
                    images.normalize_image,
                    raw_path,
                    norm_path,
                    max_edge=self._config.image_max_edge,
                )
                return await _extract_with_retry(
                    self._provider,
                    norm_path,
                    max_attempts=self._config.ai_retry_attempts,
                    base_delay=self._config.ai_retry_base_delay,
                    deadline=deadline,
                    budget=call_budget,
                )

            extraction = await asyncio.wait_for(_work(), timeout=timeout)
            receipt = validate_extraction(extraction, file_id)
            return _ReceiptOutcome(receipt=receipt)
        except BudgetExceededError:
            raise  # whole-batch abort, not a per-receipt failure
        except (asyncio.TimeoutError, TimeoutError):
            metrics.inc("timeout")
            return _ReceiptOutcome(failed=True, reason="timeout")
        except RECEIPT_FAILURE_EXCEPTIONS as exc:
            if isinstance(exc, (AIValidationError, file_validation.FileValidationError)):
                metrics.inc("validation_error")
            else:
                metrics.inc("ai_error")
            return _ReceiptOutcome(failed=True, reason=str(exc))
        except Exception as exc:  # isolate this receipt, keep the batch alive
            metrics.inc("unexpected")
            logger.exception("unexpected error on receipt %s", file_id)
            return _ReceiptOutcome(failed=True, reason=f"unexpected: {exc}")
        finally:
            metrics.observe("receipt_processing_seconds", time.monotonic() - start)
