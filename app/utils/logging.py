"""Centralized logging setup with secret redaction."""

from __future__ import annotations

import logging
import re
import sys

_SENSITIVE_PATTERNS = [
    re.compile(r"openai_api_key[=:]?\s*[\w-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)(password|api[_-]?key|token)\s*[=:]\s*\S+"),
]


def redact(message: str) -> str:
    for pat in _SENSITIVE_PATTERNS:
        message = pat.sub(r"\1=<redacted>", message) if "password" in pat.pattern.lower() else pat.sub("[REDACTED]", message)
    return message


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = tuple(redact(str(a)) if isinstance(a, str) else a for a in record.args)
            except TypeError:
                pass
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    # Ensure the redacting filter is on all handlers.
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
