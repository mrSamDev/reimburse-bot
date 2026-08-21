"""Tests for request_id correlation in logs."""

import logging

from app.utils.logging import RedactingFilter, RequestIdFormatter, request_scope


class _Capture(logging.Handler):
    def __init__(self, formatter):
        super().__init__()
        self.setFormatter(formatter)
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


def _capture():
    fmt = RequestIdFormatter("%(levelname)s %(message)s")
    h = _Capture(fmt)
    lg = logging.getLogger("test.rid")
    lg.addHandler(h)
    lg.propagate = False
    return lg, h


def test_no_request_id_outside_scope():
    lg, h = _capture()
    lg.warning("no context here")
    assert "request_id=" not in h.messages[0]


def test_request_id_appended_inside_scope():
    lg, h = _capture()
    with request_scope("abc123"):
        lg.warning("downloading receipt")
    assert "request_id=abc123" in h.messages[0]


def test_request_id_cleared_after_scope():
    lg, h = _capture()
    with request_scope("abc123"):
        lg.warning("inside")
    lg.warning("after")
    assert "request_id=abc123" in h.messages[0]
    assert "request_id=" not in h.messages[1]


def test_empty_request_id_omitted():
    lg, h = _capture()
    with request_scope(""):
        lg.warning("no id")
    assert "request_id=" not in h.messages[0]


def test_redaction_and_request_id_combined():
    lg, h = _capture()
    h.addFilter(RedactingFilter())
    with request_scope("rid42"):
        lg.warning("api key sk-testkey1234567890 leaked")
    msg = h.messages[0]
    assert "request_id=rid42" in msg
    assert "sk-testkey1234567890" not in msg


def test_nested_scopes_inner_wins_then_restores():
    lg, h = _capture()
    with request_scope("outer"):
        with request_scope("inner"):
            lg.warning("deep")
        lg.warning("outer")
    assert "request_id=inner" in h.messages[0]
    assert "request_id=outer" in h.messages[1]
