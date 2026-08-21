"""Integration tests for the processing pipeline using fakes."""

import logging
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from app.ai.base import AIProviderError, ReceiptExtraction
from app.config import Config
from app.services.ledger_service import ReceiptLedger
from app.services.receipt_service import (
    ProcessingError,
    ProcessingService,
    run_with_cleanup,
)
from app.utils.logging import RequestIdFormatter


def _make_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 120), (20, 40, 60)).save(path, format="JPEG")
    return path


class FakeTelegram:
    def __init__(self, fail_ids=(), oversized_ids=(), corrupt_ids=()):
        self.fail_ids = set(fail_ids)
        self.oversized = set(oversized_ids)
        self.corrupt = set(corrupt_ids)

    async def download_file(self, file_id, dest_path):
        if file_id in self.fail_ids:
            raise RuntimeError("telegram down")
        if file_id in self.oversized:
            Path(dest_path).write_bytes(b"0" * (11 * 1024 * 1024))
            return Path(dest_path)
        if file_id in self.corrupt:
            Path(dest_path).write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
            return Path(dest_path)
        return _make_image(Path(dest_path))


class FakeProvider:
    def __init__(self, sequence):
        self.calls = 0
        self.sequence = sequence

    def extract_receipt(self, image_path):
        item = self.sequence[self.calls % len(self.sequence)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def _config(tmp: Path, max_receipts: int = 20) -> Config:
    return Config(
        ai_provider="openai", openai_api_key="x", temp_dir=tmp, max_receipts=max_receipts,
        report_title="Heading Travel Expenses", report_period="July Expenses",
        ai_retry_base_delay=0,  # keep integration tests fast (no real backoff sleeps)
        ai_concurrency=1,  # sequential: keeps sequence-based tests deterministic
    )


def _ext(name="Ride with Sazzad", total="53.50", date="Jun 23, 2026", conf=0.95):
    return ReceiptExtraction(
        merchant_name=name, transaction_date=date, currency="AED",
        subtotal=total, tax=None, discount=None, total=total, confidence=conf,
    )


async def test_full_pipeline_success(tmp_path):
    cfg = _config(tmp_path)
    delivered = {}

    async def deliver(result):
        # PDF still exists here (before cleanup).
        delivered["exists"] = result.out_pdf_path.exists()
        delivered["total"] = result.batch.total

    svc = ProcessingService(cfg, FakeProvider([_ext("A", "53.50"), _ext("B", "51.00")]), FakeTelegram())
    result = await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir, deliver=deliver)
    assert result.processed_count == 2
    assert result.failed_count == 0
    assert result.batch.total == 104.50
    assert delivered["exists"] is True
    # After cleanup the request dir is gone.
    assert not result.request_base.exists()


async def test_single_failing_receipt_does_not_destroy_batch(tmp_path):
    cfg = _config(tmp_path)
    # First provider call raises for every retry attempt; second succeeds.
    # (3 attempts = max ai_retry_attempts, so the failing receipt gives up.)
    svc = ProcessingService(
        cfg,
        FakeProvider([
            AIProviderError("model error"),
            AIProviderError("model error"),
            AIProviderError("model error"),
            _ext("Good", "10.00"),
        ]),
        FakeTelegram(),
    )
    result = await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir)
    assert result.failed_count == 1
    assert result.processed_count == 1
    assert result.batch.total == 10.00


async def test_corrupt_receipt_marked_failed(tmp_path):
    cfg = _config(tmp_path)
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram(corrupt_ids={"bad"}))
    # Single receipt that fails => nothing to report => hard error.
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, ["bad"], cfg.temp_dir)
    leftovers = [p for p in Path(cfg.temp_dir).iterdir() if p.name.startswith("request_")]
    assert leftovers == []


async def test_all_fail_raises_and_cleans(tmp_path):
    cfg = _config(tmp_path)
    svc = ProcessingService(cfg, FakeProvider([AIProviderError("down")]), FakeTelegram())
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, ["f1"], cfg.temp_dir)
    leftovers = [p for p in Path(cfg.temp_dir).iterdir() if p.name.startswith("request_")]
    assert leftovers == []


async def test_oversized_download_rejected(tmp_path):
    cfg = _config(tmp_path)
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram(oversized_ids=["big"]))
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, ["big"], cfg.temp_dir)


async def test_empty_batch_rejected(tmp_path):
    cfg = _config(tmp_path)
    svc = ProcessingService(cfg, FakeProvider([]), FakeTelegram())
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, [], cfg.temp_dir)


async def test_processing_logs_carry_request_id(tmp_path):
    cfg = _config(tmp_path)
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram())

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.setFormatter(RequestIdFormatter("%(message)s"))
            self.lines = []

        def emit(self, record):
            self.lines.append(self.format(record))

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    svc_logger = logging.getLogger("app.services.receipt_service")
    svc_logger.setLevel(logging.DEBUG)
    svc_logger.addHandler(handler)
    try:
        result = await run_with_cleanup(svc, 1, ["f1"], cfg.temp_dir)
        assert handler.lines, "expected at least one processing log line"
        assert any(f"request_id={result.request_id}" in line for line in handler.lines)
    finally:
        svc_logger.removeHandler(handler)


async def test_too_many_receipts_rejected(tmp_path):
    cfg = _config(tmp_path, max_receipts=2)
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram())
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, ["f1", "f2", "f3"], cfg.temp_dir)


async def test_pdf_contains_receipt_descriptions(tmp_path):
    cfg = _config(tmp_path)
    delivered = {}

    async def deliver(result):
        from pypdf import PdfReader

        text = " ".join((p.extract_text() or "") for p in PdfReader(str(result.out_pdf_path)).pages)
        delivered["text"] = text

    svc = ProcessingService(
        cfg,
        FakeProvider([_ext("Ride with Sazzad", "53.50"), _ext("Bolt", "73.00")]),
        FakeTelegram(),
    )
    await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir, deliver=deliver)
    assert "Ride with Sazzad" in delivered["text"]
    assert "Bolt" in delivered["text"]
    assert "126.50" in delivered["text"]  # total


class _SlowProvider:
    def extract_receipt(self, image_path):
        time.sleep(0.03)
        return _ext("A", "10")


async def test_batch_time_budget_aborts_cleanly(tmp_path):
    cfg = _config(tmp_path)
    cfg.max_processing_seconds = 0.01  # far below what 2 slow receipts need
    svc = ProcessingService(cfg, _SlowProvider(), FakeTelegram())
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir)
    # Cleanup still ran even though the batch was aborted.
    leftovers = [p for p in Path(cfg.temp_dir).iterdir() if p.name.startswith("request_")]
    assert leftovers == []


async def test_batch_budget_zero_disables_limit(tmp_path):
    cfg = _config(tmp_path)
    cfg.max_processing_seconds = 0  # disabled
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram())
    result = await run_with_cleanup(svc, 1, ["f1"], cfg.temp_dir)
    assert result.processed_count == 1


async def test_on_progress_callback_receives_events(tmp_path):
    cfg = _config(tmp_path)
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10"), _ext("B", "20")]), FakeTelegram())
    events = []

    async def progress(done, total):
        events.append((done, total))

    result = await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir, on_progress=progress)
    assert events == [(1, 2), (2, 2)]
    assert result.processed_count == 2


async def test_receipt_failures_collected(tmp_path):
    cfg = _config(tmp_path)
    # f1 fails every retry attempt; f2 succeeds.
    svc = ProcessingService(
        cfg,
        FakeProvider([AIProviderError("AI error")] * 3 + [_ext("ok", "10")]),
        FakeTelegram(),
    )
    result = await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir)
    assert result.failed_count == 1
    assert len(result.receipt_failures) == 1
    assert result.receipt_failures[0]["file_id"] == "f1"
    assert result.receipt_failures[0]["reason"]


class _TrackingProvider:
    """Counts peak concurrent extract_receipt calls (thread-safe)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._now = 0
        self.peak = 0

    def extract_receipt(self, image_path):
        with self._lock:
            self._now += 1
            self.peak = max(self.peak, self._now)
        time.sleep(0.03)
        with self._lock:
            self._now -= 1
        return _ext("A", "10")


async def test_ai_concurrency_capped(tmp_path):
    cfg = _config(tmp_path, max_receipts=10)
    cfg.ai_concurrency = 2
    provider = _TrackingProvider()
    svc = ProcessingService(cfg, provider, FakeTelegram())
    await run_with_cleanup(svc, 1, [f"f{i}" for i in range(6)], cfg.temp_dir)
    # The cap invariant: never more than `ai_concurrency` in flight at once.
    assert provider.peak <= 2
    # With 6 slow receipts the pipeline did overlap (parallelism actually happened).
    assert provider.peak >= 2


async def test_ledger_persists_accepted_receipt(tmp_path):
    cfg = _config(tmp_path)
    ledger = ReceiptLedger(tmp_path / "ledger.db")
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram(), ledger=ledger)
    result = await run_with_cleanup(svc, 1, ["f1"], cfg.temp_dir)
    assert ledger.count() == 1
    row = ledger.all()[0]
    assert row["merchant_name"] == "A"
    assert row["total"] == Decimal("10")
    assert row["user_id"] == 1
    assert row["request_id"] == result.request_id


async def test_ledger_idempotent_across_rerun(tmp_path):
    cfg = _config(tmp_path)
    ledger = ReceiptLedger(tmp_path / "ledger.db")
    svc = ProcessingService(cfg, FakeProvider([_ext("A", "10")]), FakeTelegram(), ledger=ledger)
    await run_with_cleanup(svc, 1, ["f1"], cfg.temp_dir)
    await run_with_cleanup(svc, 1, ["f1"], cfg.temp_dir)
    assert ledger.count() == 1  # same file_id deduplicated


async def test_ledger_records_multiple_receipts(tmp_path):
    cfg = _config(tmp_path)
    ledger = ReceiptLedger(tmp_path / "ledger.db")
    svc = ProcessingService(
        cfg,
        FakeProvider([_ext("A", "10"), _ext("B", "20")]),
        FakeTelegram(),
        ledger=ledger,
    )
    await run_with_cleanup(svc, 1, ["f1", "f2"], cfg.temp_dir)
    assert ledger.count() == 2
    assert ledger.all()[1]["total"] == Decimal("20")
