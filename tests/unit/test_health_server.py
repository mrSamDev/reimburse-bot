"""Tests for the stdlib health + metrics HTTP server."""

import json
import threading
import urllib.request

from app.services.health_server import create_health_server
from app.utils import metrics


def test_health_and_metrics_endpoints():
    metrics.reset_metrics()
    metrics.inc("processed", 3)
    server = create_health_server(host="127.0.0.1", port=0)  # ephemeral port
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        health = json.loads(urllib.request.urlopen(f"{base}/health", timeout=3).read())
        assert health["status"] == "ok"
        m = json.loads(urllib.request.urlopen(f"{base}/metrics", timeout=3).read())
        assert m["processed"] == 3
        # Unknown path -> 404.
        try:
            urllib.request.urlopen(f"{base}/nope", timeout=3)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


def _get(base, path, headers=None):
    req = urllib.request.Request(f"{base}{path}", headers=headers or {})
    return urllib.request.urlopen(req, timeout=3).read()


def test_metrics_requires_token_when_configured():
    server = create_health_server(host="127.0.0.1", port=0, token="s3cret")
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # /health stays open (liveness probe).
        health = json.loads(_get(base, "/health"))
        assert health["status"] == "ok"
        # /metrics without a token -> 401.
        try:
            _get(base, "/metrics")
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        # /metrics with a wrong token -> 401.
        try:
            _get(base, "/metrics", {"Authorization": "Bearer wrong"})
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        # /metrics with the correct Bearer token -> 200.
        body = json.loads(_get(base, "/metrics", {"Authorization": "Bearer s3cret"}))
        assert "processed" in body
    finally:
        server.shutdown()
        server.server_close()
