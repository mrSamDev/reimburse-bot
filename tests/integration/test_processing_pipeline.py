"""Integration tests for the processing pipeline using fakes."""

from pathlib import Path

import pytest
from PIL import Image

from app.ai.base import AIProviderError, ReceiptExtraction
from app.config import Config
from app.services import file_validation
from app.services.receipt_service import (
    ProcessingError,
    ProcessingService,
    run_with_cleanup,
)


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
    # First provider call raises; second succeeds.
    svc = ProcessingService(
        cfg,
        FakeProvider([AIProviderError("model error"), _ext("Good", "10.00")]),
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
