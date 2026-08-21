"""Ollama Cloud vision provider (OpenAI-compatible endpoint)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from openai import RateLimitError  # noqa: E402  (hard runtime dependency)

from app.ai.base import AIProviderError, AIRateLimitError, ReceiptExtraction, ReceiptVisionProvider
from app.ai.openai_provider import (
    OpenAIProvider,
    _extract_json,
    _parse_retry_after,
    _retry_after_header,
)
from app.config import Config

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "receipt_extraction.txt"


class OllamaProvider(ReceiptVisionProvider):
    """Talks to an Ollama server exposing an OpenAI-compatible /v1 endpoint.

    Vision-capable Ollama models (e.g. ``llava``, ``llama3.2-vision``) accept an
    ``image_url`` data-URI in the OpenAI chat-completions format.
    """

    def __init__(self, config: Config) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=config.ollama_api_key or "ollama",
            base_url=config.ollama_base_url,
            # max_retries=0: disable the SDK's built-in auto-retry so the app's
            # own pacing/backoff (see _extract_with_retry) is the only retry
            # layer, matching OpenAIProvider.
            max_retries=0,
        )
        self._model = config.ollama_model or "llava"
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
        }
        try:
            resp = self._client.chat.completions.create(**payload, timeout=self._timeout)
            content = resp.choices[0].message.content or ""
            parsed = _extract_json(content)
            return ReceiptExtraction(**parsed)
        except AIProviderError:
            raise
        except RateLimitError as exc:
            # Surface 429s as AIRateLimitError so the shared rate-limit backoff
            # (Retry-After / TPM-window handling) applies, matching OpenAIProvider.
            err_type = getattr(exc, "type", None) or getattr(exc, "code", None)
            logger.info("ollama 429: type=%r code=%r", err_type, getattr(exc, "code", None))
            raise AIRateLimitError(
                f"Ollama rate limited [{err_type or 'unknown'}]: {exc}",
                retry_after=_parse_retry_after(_retry_after_header(exc)),
                kind=err_type,
            ) from exc
        except Exception as exc:
            raise AIProviderError(f"Ollama request failed: {exc}") from exc


def build_provider(config: Config) -> ReceiptVisionProvider:
    """Factory returning the configured provider."""
    if config.ai_provider == "openai":
        return OpenAIProvider(config)
    if config.ai_provider == "ollama":
        return OllamaProvider(config)
    raise AIProviderError(f"Unsupported provider: {config.ai_provider}")
