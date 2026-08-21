"""Zero-dependency health + metrics HTTP server (stdlib only)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.utils import metrics

logger = logging.getLogger(__name__)


def _make_handler(metrics_provider: Callable[[], dict]):
    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path in ("/health", "/health/"):
                self._send(200, {"status": "ok"})
            elif self.path in ("/metrics", "/metrics/"):
                self._send(200, metrics_provider())
            else:
                self._send(404, {"error": "not found"})

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:  # quieter default
            logger.info("health: " + fmt % args)

    return _HealthHandler


def create_health_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    metrics_provider: Callable[[], dict] = metrics.get_metrics,
) -> ThreadingHTTPServer:
    """Return a ``ThreadingHTTPServer`` serving ``/health`` and ``/metrics``."""
    return ThreadingHTTPServer((host, port), _make_handler(metrics_provider))
