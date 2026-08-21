"""Centralized logging setup with secret redaction and request correlation."""

from __future__ import annotations

import contextvars
import logging
import re
import sys
import traceback
from contextlib import contextmanager

# The id of the in-flight receipt-processing request. When set, every log
# record emitted within the scope is tagged so failures are attributable.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


@contextmanager
def request_scope(request_id: str):
    """Tag all logs emitted within this block with ``request_id``."""
    token = _request_id.set(request_id or "")
    try:
        yield
    finally:
        _request_id.reset(token)


class RequestIdFormatter(logging.Formatter):
    """Formatter that appends ``[request_id=<id>]`` when a request is in scope."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        rid = _request_id.get()
        if rid:
            return f"{msg} [request_id={rid}]"
        return msg


class JSONFormatter(logging.Formatter):
    """Structured JSON log line, carrying ``request_id`` when in scope."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        data: dict = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get() or None,
        }
        exc_text = getattr(record, "exc_text", None)
        if record.exc_info and not exc_text:
            exc_text = self.formatException(record.exc_info)
        if exc_text:
            data["exception"] = exc_text
        return json.dumps(data)


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
        if record.exc_info:
            # Scrub the formatted traceback so a secret on the stack never leaks
            # through the exception path (message alone is not sufficient).
            try:
                record.exc_text = redact(
                    "".join(traceback.format_exception(*record.exc_info))
                )
                record.exc_info = None
            except Exception:
                pass
        return True


def configure_logging(level: str = "INFO", log_format: str = "text") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = RequestIdFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    else:
        # Replace the formatter on existing handlers so log_format takes effect
        # even when configure_logging is called more than once.
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler):
                h.setFormatter(formatter)
    # Ensure the redacting filter is on all handlers.
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
