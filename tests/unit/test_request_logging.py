"""Tests for request_id correlation in logs."""

import json
import logging

from app.utils.logging import JSONFormatter, RedactingFilter, RequestIdFormatter, request_scope


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


def _json_capture():
    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.setFormatter(JSONFormatter())
            self.lines = []

        def emit(self, record):
            self.lines.append(self.format(record))

    h = _Capture()
    lg = logging.getLogger("test.json")
    lg.addHandler(h)
    lg.propagate = False
    return lg, h


def test_json_formatter_includes_request_id_when_in_scope():
    lg, h = _json_capture()
    with request_scope("rid99"):
        lg.warning("processing receipt")
    obj = json.loads(h.lines[0])
    assert obj["request_id"] == "rid99"
    assert obj["message"] == "processing receipt"
    assert obj["level"] == "WARNING"


def test_json_formatter_request_id_none_outside_scope():
    lg, h = _json_capture()
    lg.warning("no scope")
    assert json.loads(h.lines[0])["request_id"] is None
