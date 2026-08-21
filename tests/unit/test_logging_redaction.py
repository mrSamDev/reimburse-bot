"""Tests for logging redaction (never log secrets or receipts)."""

import logging

from app.utils.logging import RedactingFilter, configure_logging, redact


def test_redacts_api_key():
    out = redact("using sk-abcdef1234567890abc")
    assert "sk-abcdef1234567890abc" not in out


def test_redacts_openai_key_field():
    out = redact("OPENAI_API_KEY=sk-super-secret-value-123")
    assert "sk-super-secret-value-123" not in out


def test_redacts_password_assignment():
    out = redact("password=hunter2extra")
    assert "hunter2extra" not in out


def test_redacts_plain_text_untouched():
    out = redact("processed 5 receipts for user 123")
    assert "processed 5 receipts" in out


def test_redacting_filter_applies_to_records():
    import logging as _l

    class _Handler(_l.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    h = _Handler()
    h.addFilter(RedactingFilter())
    logger = _l.getLogger("test.redact2")
    logger.addHandler(h)
    logger.propagate = False
    logger.info("api key is sk-testkey12345 and password=pw")
    joined = " ".join(h.messages)
    assert "sk-testkey12345" not in joined
    assert "pw" not in joined


def test_configure_logging_adds_filter():
    configure_logging()
    root = logging.getLogger()
    # At least one handler carries a RedactingFilter.
    assert any(
        isinstance(f, RedactingFilter)
        for h in root.handlers
        for f in h.filters
    )
