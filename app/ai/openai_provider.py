"""OpenAI vision provider (image_url + JSON schema via the openai SDK)."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from app.ai.base import AIProviderError, ReceiptExtraction, ReceiptVisionProvider
from app.config import Config

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "receipt_extraction.txt"


class OpenAIProvider(ReceiptVisionProvider):
    def __init__(self, config: Config) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=config.openai_api_key)
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
        except Exception as exc:
            raise AIProviderError(f"OpenAI request failed: {exc}") from exc


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
            raise AIProviderError("AI returned no parseable JSON")
        obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise AIProviderError("AI response was not a JSON object")
    return obj
