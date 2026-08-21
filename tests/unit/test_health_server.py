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
