"""Tests for the TelegramService wrapper using a fake bot."""

import pytest

from app.services.file_validation import OversizedFileError
from app.services.telegram_service import TelegramService
from tests.conftest import make_image


class _File:
    def __init__(self, size=None):
        self.file_size = size
        self._downloaded = False

    async def download_to_drive(self, dest, timeout=None):
        self._downloaded = True
        make_image(dest, "JPEG")


class FakeBot:
    def __init__(self, file_size=None, fail_get=False, send_error=False):
        self.file = _File(size=file_size)
        self.fail_get = fail_get
        self.send_error = send_error
        self.sent = []

    async def get_file(self, file_id):
        if self.fail_get:
            raise RuntimeError("api down")
        return self.file

    async def send_document(self, chat_id, document=None, caption="", timeout=None):
        if self.send_error:
            raise RuntimeError("send failed")
        self.sent.append(caption)

    async def delete_message(self, chat_id, message_id):
        raise RuntimeError("cannot delete")


async def test_successful_download(tmp_path):
    svc = TelegramService(FakeBot(file_size=1000), timeout=30, max_file_size_mb=10)
    dest = tmp_path / "a.img"
    await svc.download_file("f1", dest)
    assert dest.exists()


async def test_download_timeout_propagates(tmp_path):
    class TimeoutBot:
        async def get_file(self, file_id):
            raise TimeoutError("timeout")

    svc = TelegramService(TimeoutBot())
    with pytest.raises(TimeoutError):
        await svc.download_file("f", tmp_path / "x")


async def test_api_failure_propagates(tmp_path):
    svc = TelegramService(FakeBot(fail_get=True))
    with pytest.raises(RuntimeError):
        await svc.download_file("f", tmp_path / "x")


async def test_oversized_rejected_before_download(tmp_path):
    bot = FakeBot(file_size=11 * 1024 * 1024)
    svc = TelegramService(bot, max_file_size_mb=10)
    with pytest.raises(OversizedFileError):
        await svc.download_file("f", tmp_path / "x")
    assert bot.file._downloaded is False  # never buffered the oversized file


async def test_send_document(tmp_path):
    bot = FakeBot()
    svc = TelegramService(bot)
    doc = make_image(tmp_path / "r.pdf", "JPEG")
    await svc.send_document(123, doc, caption="hello")
    assert bot.sent == ["hello"]


async def test_send_document_failure_raises(tmp_path):
    bot = FakeBot(send_error=True)
    svc = TelegramService(bot)
    doc = make_image(tmp_path / "r.pdf", "JPEG")
    with pytest.raises(RuntimeError):
        await svc.send_document(1, doc)


async def test_delete_message_best_effort(tmp_path):
    svc = TelegramService(FakeBot())
    # Should not raise despite deletion failure.
    await svc.delete_message(1, 2)
