"""Zero-dependency health + metrics HTTP server (stdlib only)."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.utils import metrics

logger = logging.getLogger(__name__)


def _make_handler(metrics_provider: Callable[[], dict], *, token: str = ""):
    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path in ("/health", "/health/"):
                # Liveness: always open so orchestrator probes work without a token.
                self._send(200, {"status": "ok"})
            elif self.path in ("/metrics", "/metrics/"):
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                    return
                self._send(200, metrics_provider())
            else:
                self._send(404, {"error": "not found"})

        def _authorized(self) -> bool:
            """No token configured -> open. Otherwise require ``Authorization: Bearer``."""
            if not token:
                return True
            provided = self.headers.get("Authorization") or ""
            if provided.startswith("Bearer "):
                provided = provided[len("Bearer "):]
            return hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))

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
    *,  # keyword-only: no positional token to avoid breaking existing callers
    token: str = "",
) -> ThreadingHTTPServer:
    """Return a ``ThreadingHTTPServer`` serving ``/health`` and ``/metrics``.

    ``token``, when set, gates ``/metrics`` behind an ``Authorization: Bearer``
    header (constant-time compare). ``/health`` is always open for liveness.
    """
    return ThreadingHTTPServer((host, port), _make_handler(metrics_provider, token=token))
