"""OpenAI vision provider (image_url + JSON schema via the openai SDK)."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from app.ai.base import (
    AIProviderError,
    AIRateLimitError,
    ReceiptExtraction,
    ReceiptVisionProvider,
)
from app.config import Config

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "receipt_extraction.txt"

from openai import RateLimitError  # noqa: E402  (hard runtime dependency)


class OpenAIProvider(ReceiptVisionProvider):
    def __init__(self, config: Config) -> None:
        from openai import OpenAI

        # max_retries=0: the SDK's built-in auto-retry is disabled so we fully
        # control pacing/backoff (see _extract_with_retry in receipt_service).
        self._client = OpenAI(api_key=config.openai_api_key, max_retries=0)
        self._model = config.openai_model or "gpt-4o-mini"
        self._timeout = config.ai_timeout_seconds
        self._prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def extract_receipt(self, image_path: str | Path) -> ReceiptExtraction:
        image_path = Path(image_path)
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        data_url = f"data:image/jpeg;base64,{b64}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 500,
        }
        try:
            resp = self._client.chat.completions.create(
                **payload, timeout=self._timeout
            )
            content = resp.choices[0].message.content or ""
            parsed = _extract_json(content)
            return ReceiptExtraction(**parsed)
        except AIProviderError:
            raise
        except RateLimitError as exc:
            raise AIRateLimitError(
                f"OpenAI rate limited: {exc}", retry_after=_parse_retry_after(_retry_after_header(exc))
            ) from exc
        except Exception as exc:
            raise AIProviderError(f"OpenAI request failed: {exc}") from exc


def _retry_after_header(exc) -> str | None:
    """Pull the Retry-After header off an OpenAI status error, if present."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = response.headers if hasattr(response, "headers") else {}
    return headers.get("retry-after") or headers.get("Retry-After")


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After into seconds.

    Accepts a decimal seconds string ("0.38") or an HTTP-date form
    ("Wed, 21 Oct 2015 07:28:00 GMT"). Returns None when unparseable.
    """
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _extract_json(content: str) -> dict[str, Any]:
    """Best-effort JSON extraction from a possibly-markdown-wrapped response."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first {...} block.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise AIProviderError("AI returned no parseable JSON") from None
        obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise AIProviderError("AI response was not a JSON object")
    return obj
