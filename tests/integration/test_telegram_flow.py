"""End-to-end bot flow test using fakes (no real Telegram)."""

import asyncio
import logging
import time
from pathlib import Path

from PIL import Image

from app.ai.base import AIProviderError, ReceiptExtraction
from app.bot.bot import ReimbursementBot
from app.bot.states import BotState
from app.config import Config
from app.services.receipt_service import ProcessingService
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService
from app.utils.logging import RequestIdFormatter


def make_valid_image(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 120), (10, 20, 30)).save(path, format="JPEG")


class FakeFile:
    def __init__(self, size=1000):
        self.file_size = size

    async def download_to_drive(self, dest, timeout=None, read_timeout=None, write_timeout=None):
        make_valid_image(dest)


class FakeTransport:
    def __init__(self):
        self.get_file_calls = 0
        self.sent_docs = []
        self.deleted = []

    async def get_file(self, file_id):
        self.get_file_calls += 1
        return FakeFile()

    async def send_document(self, chat_id, document=None, caption="", timeout=None, read_timeout=None, write_timeout=None):
        self.sent_docs.append(caption)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def extract_receipt(self, image_path):
        self.calls += 1
        return ReceiptExtraction(
            merchant_name="Ride with Sazzad",
            transaction_date="Tue, Jun 23, 2026",
            currency="AED",
            total="53.50",
            confidence=0.95,
        )


class FakePhoto:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeDocument:
    def __init__(self, mime_type="application/pdf", file_id="doc1"):
        self.mime_type = mime_type
        self.file_id = file_id


class FakeMessage:
    def __init__(self, text="", photo=None, document=None, message_id=1):
        self.text = text
        self.photo = photo
        self.document = document
        self.message_id = message_id
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return text


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeChat:
    def __init__(self, cid):
        self.id = cid


class FakeUpdate:
    def __init__(self, message, user_id=111, chat_id=111):
        self.effective_user = FakeUser(user_id)
        self.effective_chat = FakeChat(chat_id)
        self.effective_message = message
        self.message = message


def _build(tmp_path, allowed="111", password="secret"):
    config = Config(
        telegram_token="t", allowed_user_ids=allowed, bot_password=password,
        ai_provider="openai", openai_api_key="k", temp_dir=tmp_path,
        max_receipts=20, ai_request_delay_seconds=0,
        report_title="Heading Travel Expenses", report_period="July Expenses",
    )
    transport = FakeTransport()
    telegram = TelegramService(transport, timeout=30, max_file_size_mb=10)
    security = SecurityService(config)
    sessions = SessionStore(db_path=tmp_path / "sessions.db")
    provider = FakeProvider()
    processing = ProcessingService(config, provider, telegram)
    bot = ReimbursementBot(config, security, sessions, telegram, provider, processing)
    return bot, transport, sessions


def _photo_update(fid, uid=111):
    return FakeUpdate(FakeMessage(photo=[FakePhoto(fid)]), user_id=uid)


def _text_update(text, uid=111):
    return FakeUpdate(FakeMessage(text=text), user_id=uid)


async def test_full_authorized_flow(tmp_path):
    bot, transport, sessions = _build(tmp_path)

    await bot.start_command(_text_update("/start"), None)
    assert (await sessions.get(111)).state == BotState.IDLE

    await bot.message_handler(_photo_update("f1"), None)
    await bot.message_handler(_photo_update("f2"), None)
    session = await sessions.get(111)
    assert session.state == BotState.COLLECTING
    assert session.receipt_file_ids == ["f1", "f2"]

    status_msg = FakeMessage("/status")
    await bot.status_command(FakeUpdate(status_msg), None)
    assert "Receipts staged: 2" in status_msg.replies[-1]

    gen_msg = FakeMessage("/generate")
    await bot.generate_command(FakeUpdate(gen_msg), None)
    assert (await sessions.get(111)).state == BotState.AWAITING_HEADING
    assert "heading" in gen_msg.replies[-1].lower()

    await bot.message_handler(_text_update("July Expenses"), None)
    assert (await sessions.get(111)).state == BotState.AWAITING_PASSWORD

    await bot.message_handler(_text_update("secret"), None)
    assert len(transport.sent_docs) == 1
    caption = transport.sent_docs[0]
    assert "Receipts: 2" in caption
    assert "AED Total: 107.00" in caption
    session = await sessions.get(111)
    assert session.state == BotState.IDLE
    assert session.receipt_file_ids == []
    leftovers = [p for p in Path(tmp_path).iterdir() if p.name.startswith("request_")]
    assert leftovers == []


async def test_unauthorized_user_rejected(tmp_path):
    bot, _, _ = _build(tmp_path)
    msg = FakeMessage("/start")
    await bot.start_command(FakeUpdate(msg, user_id=999), None)
    assert "not authorized" in msg.replies[-1].lower()


async def test_wrong_password_returns_to_idle(tmp_path):
    bot, transport, sessions = _build(tmp_path)
    await bot.start_command(_text_update("/start"), None)
    await bot.message_handler(_photo_update("f1"), None)
    gen_msg = FakeMessage("/generate")
    await bot.generate_command(FakeUpdate(gen_msg), None)
    assert (await sessions.get(111)).state == BotState.AWAITING_HEADING
    await bot.message_handler(_text_update("July Expenses"), None)
    assert (await sessions.get(111)).state == BotState.AWAITING_PASSWORD
    await bot.message_handler(_text_update("wrongpass"), None)
    session = await sessions.get(111)
    assert session.state == BotState.IDLE
    assert transport.sent_docs == []
    # The password message was best-effort deleted.
    assert transport.deleted


async def test_unsupported_document_rejected(tmp_path):
    bot, _, sessions = _build(tmp_path)
    await bot.start_command(_text_update("/start"), None)
    doc_msg = FakeMessage(document=FakeDocument(mime_type="application/pdf"))
    update = FakeUpdate(doc_msg)
    await bot.message_handler(update, None)
    assert (await sessions.get(111)).receipt_file_ids == []
    assert doc_msg.replies and "image" in doc_msg.replies[-1].lower()


async def test_duplicate_receipt_not_staged(tmp_path):
    bot, _, sessions = _build(tmp_path)
    await bot.start_command(_text_update("/start"), None)
    await bot.message_handler(_photo_update("f1"), None)
    await bot.message_handler(_photo_update("f1"), None)
    assert (await sessions.get(111)).receipt_file_ids == ["f1"]


async def test_report_caption_surfaces_failed_receipts(tmp_path):
    class _FailOnceProvider:
        def __init__(self):
            self.calls = 0

        def extract_receipt(self, image_path):
            self.calls += 1
            if self.calls <= 3:  # f1 fails all retry attempts
                raise AIProviderError("provider error")
            return ReceiptExtraction(merchant_name="Ride with Sazzad", total="53.50", confidence=0.95)

    config = Config(
        telegram_token="t", allowed_user_ids="111", bot_password="secret",
        ai_provider="openai", openai_api_key="k", temp_dir=tmp_path,
        max_receipts=20, ai_retry_base_delay=0, ai_concurrency=1, ai_request_delay_seconds=0,
        report_title="Heading Travel Expenses", report_period="July Expenses",
    )
    transport = FakeTransport()
    telegram = TelegramService(transport, timeout=30, max_file_size_mb=10)
    security = SecurityService(config)
    sessions = SessionStore(db_path=tmp_path / "sessions.db")
    provider = _FailOnceProvider()
    processing = ProcessingService(config, provider, telegram)
    bot = ReimbursementBot(config, security, sessions, telegram, provider, processing)

    await bot.start_command(_text_update("/start"), None)
    await bot.message_handler(_photo_update("f1"), None)
    await bot.message_handler(_photo_update("f2"), None)
    await bot.generate_command(FakeUpdate(FakeMessage("/generate")), None)
    await bot.message_handler(_text_update("July Expenses"), None)
    await bot.message_handler(_text_update("secret"), None)

    assert transport.sent_docs
    assert "Could not process 1 receipt(s)" in transport.sent_docs[-1]
    assert "Receipts: 1" in transport.sent_docs[-1]


async def test_catch_all_error_log_carries_request_id(tmp_path):
    class _FailingSend(FakeTransport):
        async def send_document(self, chat_id, document=None, caption="", timeout=None, read_timeout=None, write_timeout=None):
            raise RuntimeError("telegram send exploded")

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.setFormatter(RequestIdFormatter("%(message)s"))
            self.lines = []

        def emit(self, record):
            self.lines.append(self.format(record))

    import re as _re

    config = Config(
        telegram_token="t", allowed_user_ids="111", bot_password="secret",
        ai_provider="openai", openai_api_key="k", temp_dir=tmp_path,
        max_receipts=20, ai_request_delay_seconds=0,
        report_title="Heading Travel Expenses", report_period="July Expenses",
    )
    transport = _FailingSend()
    telegram = TelegramService(transport, timeout=30, max_file_size_mb=10)
    security = SecurityService(config)
    sessions = SessionStore(db_path=tmp_path / "sessions.db")
    provider = FakeProvider()
    processing = ProcessingService(config, provider, telegram)
    bot = ReimbursementBot(config, security, sessions, telegram, provider, processing)

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    bot_logger = logging.getLogger("app.bot.bot")
    bot_logger.setLevel(logging.DEBUG)
    bot_logger.addHandler(handler)
    try:
        await bot.start_command(_text_update("/start"), None)
        await bot.message_handler(_photo_update("f1"), None)
        await bot.generate_command(FakeUpdate(FakeMessage("/generate")), None)
        await bot.message_handler(_text_update("July Expenses"), None)
        await bot.message_handler(_text_update("secret"), None)

        catch_all = [ln for ln in handler.lines if "unhandled processing error" in ln]
        assert catch_all, "expected the bot catch-all to log"
        assert _re.search(r"\[request_id=[0-9a-f]{6}\]", catch_all[0]), catch_all[0]
    finally:
        bot_logger.removeHandler(handler)


async def test_concurrent_message_during_generation_is_busy(tmp_path):
    """Regression (Bug B): a message arriving mid-generation must be rejected as
    busy — NOT misinterpreted as a password attempt, deleted, or reset to IDLE.

    ``PROCESSING`` must be persisted before the long-running extraction starts so
    that ``message_handler`` routes a concurrent message to the busy path.
    """
    class _SlowProvider(FakeProvider):
        def extract_receipt(self, image_path):
            time.sleep(0.4)  # hold generation open so a message can interleave
            return super().extract_receipt(image_path)

    config = Config(
        telegram_token="t", allowed_user_ids="111", bot_password="secret",
        ai_provider="openai", openai_api_key="k", temp_dir=tmp_path,
        max_receipts=20, ai_request_delay_seconds=0,
        report_title="Heading Travel Expenses", report_period="July Expenses",
    )
    transport = FakeTransport()
    telegram = TelegramService(transport, timeout=30, max_file_size_mb=10)
    security = SecurityService(config)
    sessions = SessionStore(db_path=tmp_path / "sessions.db")
    provider = _SlowProvider()
    processing = ProcessingService(config, provider, telegram)
    bot = ReimbursementBot(config, security, sessions, telegram, provider, processing)

    await bot.start_command(_text_update("/start"), None)
    await bot.message_handler(_photo_update("f1"), None)
    await bot.generate_command(FakeUpdate(FakeMessage("/generate")), None)
    await bot.message_handler(_text_update("July Expenses"), None)

    # Correct password starts generation; it blocks inside a worker thread.
    gen_task = asyncio.create_task(bot.message_handler(_text_update("secret"), None))
    await asyncio.sleep(0.1)  # let it acquire the lease and persist PROCESSING

    # Same user sends another photo mid-generation.
    busy_msg = FakeMessage(photo=[FakePhoto("f2")], message_id=99)
    await bot.message_handler(FakeUpdate(busy_msg), None)

    # Rejected as busy, not treated as a password attempt.
    assert any("Please wait" in r for r in busy_msg.replies), busy_msg.replies
    assert 99 not in transport.deleted, "concurrent message must not be deleted"
    # Flow not reset: still PROCESSING, receipts intact.
    live = await sessions.get(111)
    assert live.state == BotState.PROCESSING, live.state
    assert live.receipt_file_ids == ["f1"]

    await gen_task
    assert len(transport.sent_docs) == 1, "the original generation must still deliver"
    finished = await sessions.get(111)
    assert finished.state == BotState.IDLE
    assert finished.receipt_file_ids == []


async def test_lease_renewed_during_long_generation(tmp_path):
    """A generation running longer than the lease TTL must renew the
    cross-process lease (heartbeat) so a rival instance cannot double-process.

    Regression for the lease-vs-runtime race: with a tiny lease TTL shorter than
    the run, a second SessionStore (another process) must still be unable to
    steal the slot while the run is in flight, and only re-acquire it after the
    generation completes and releases the lease.
    """
    class _SlowProvider(FakeProvider):
        def extract_receipt(self, image_path):
            time.sleep(2.4)  # hold generation open past the tiny lease TTL
            return super().extract_receipt(image_path)

    config = Config(
        telegram_token="t", allowed_user_ids="111", bot_password="secret",
        ai_provider="openai", openai_api_key="k", temp_dir=tmp_path,
        max_receipts=20, ai_request_delay_seconds=0, ai_concurrency=1,
        session_lease_ttl_seconds=1,  # shorter than the ~2.4s run
        report_title="Heading Travel Expenses", report_period="July Expenses",
    )
    transport = FakeTransport()
    telegram = TelegramService(transport, timeout=30, max_file_size_mb=10)
    security = SecurityService(config)
    sessions = SessionStore(
        db_path=tmp_path / "sessions.db",
        ttl_seconds=1800, lease_ttl_seconds=1,
    )
    provider = _SlowProvider()
    processing = ProcessingService(config, provider, telegram)
    bot = ReimbursementBot(config, security, sessions, telegram, provider, processing)

    await bot.start_command(_text_update("/start"), None)
    await bot.message_handler(_photo_update("f1"), None)
    await bot.generate_command(FakeUpdate(FakeMessage("/generate")), None)
    await bot.message_handler(_text_update("July Expenses"), None)

    gen_task = asyncio.create_task(bot.message_handler(_text_update("secret"), None))
    # Wait until the generation has acquired the lease and started the slow run.
    await asyncio.sleep(1.6)  # > lease_ttl (1s) without a heartbeat it'd be stale

    # A rival instance must NOT reclaim the slot mid-run: the heartbeat renewed it.
    rival = SessionStore(
        db_path=tmp_path / "sessions.db",
        ttl_seconds=1800, lease_ttl_seconds=1,
    )
    assert await rival.try_acquire_processing(111) is False

    await gen_task
    assert len(transport.sent_docs) == 1
    # After completion the lease is released and re-acquirable.
    assert await rival.try_acquire_processing(111) is True
