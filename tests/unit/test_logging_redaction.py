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


def test_redaction_scrubs_exception_traceback():
    import logging as _l

    class _Handler(_l.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(self.format(record))

    h = _Handler()
    h.setFormatter(_l.Formatter("%(message)s"))
    h.addFilter(RedactingFilter())
    logger = _l.getLogger("test.redact_exc")
    logger.addHandler(h)
    logger.propagate = False

    secret = "sk-excsecretkey1234567890"
    try:
        raise ValueError(f"auth failed with key {secret}")
    except ValueError:
        logger.exception("request failed")

    joined = "\n".join(h.messages)
    # The secret must be gone from BOTH the message and the formatted traceback.
    assert secret not in joined
    assert "ValueError" in joined  # traceback still rendered



def test_sensitive_patterns_carry_explicit_replacements():
    """Each sensitive pattern carries its own replacement template, not selected
    by substring-matching the pattern source.

    This is a regression guard for the brittle heuristic where the replacement
    strategy was chosen via ``"password" in pat.pattern.lower()``. With explicit
    (pattern, replacement) tuples, adding a new pattern with a capture group
    that doesn't contain 'password' in its source won't silently break.
    """
    from app.utils.logging import _SENSITIVE_PATTERNS

    for entry in _SENSITIVE_PATTERNS:
        assert isinstance(entry, tuple), (
            "patterns should be (compiled_pattern, replacement) tuples, "
            "not bare compiled patterns"
        )
        assert len(entry) == 2, "each entry is (pattern, replacement)"
        pattern, replacement = entry
        assert hasattr(pattern, "sub"), "first element is a compiled regex"
        assert isinstance(replacement, str), "second element is a replacement string"


def test_redacts_quoted_password_with_spaces():
    """Quoted password values containing spaces are fully redacted — no leakage."""
    out = redact('password = "hunter 2 extra"')
    assert "hunter" not in out
    assert "2 extra" not in out


def test_redacts_single_quoted_token_with_spaces():
    """Single-quoted token values containing spaces are fully redacted."""
    out = redact("api_key = 'my secret key'")
    assert "my secret key" not in out
    assert "secret" not in out


def test_redacts_short_api_key_value():
    """Short api_key values are redacted regardless of length."""
    out = redact("openai_api_key=abc")
    assert "abc" not in out


def test_redact_stops_at_comma():
    """Redaction stops at a comma so unrelated fields on the same line survive."""
    out = redact("password=hunter2, user_id=123")
    assert "123" in out  # user_id not redacted


def test_redacts_bearer_token():
    """Authorization: Bearer tokens are redacted."""
    out = redact("Authorization: Bearer abc123def456")
    assert "abc123def456" not in out


def test_redacts_basic_auth_header():
    """Authorization: Basic credentials are redacted."""
    out = redact("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in out


def test_redacts_openai_key_with_dots():
    """openai_api_key values with dots are fully redacted (via generic pattern)."""
    out = redact("openai_api_key=abc.def")
    assert "abc" not in out
    assert "def" not in out


def test_redact_stops_at_next_key_value():
    """Redaction stops before a whitespace-delimited key=value on the same line."""
    out = redact("token=abc user_id=123")
    assert "123" in out  # user_id not redacted
    assert "abc" not in out  # token value redacted


def test_unquoted_multi_word_value_limitation():
    """Known limitation: unquoted multi-word values only redact the first token.
    Use quoted values for multi-word secrets."""
    out = redact("password = hunter 2 extra")
    assert "hunter" not in out  # first token redacted
    # "2 extra" is NOT redacted — documented limitation
