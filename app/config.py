"""Application configuration loaded from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_PROVIDERS = {"openai", "ollama"}
SUPPORTED_IMAGE_FORMATS = {"image/jpeg", "image/png", "image/webp"}


class ConfigError(Exception):
    """Raised when configuration is invalid or incomplete."""


class Config(BaseModel):
    """Application configuration.

    Values are read from the environment (with .env support handled by the
    ``load`` factory). All fields have safe defaults except credentials.
    """

    model_config = ConfigDict(extra="forbid")

    telegram_token: str = ""
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_chat_ids: list[int] = Field(default_factory=list)
    bot_password: str = ""
    ai_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_api_key: str = ""
    ollama_model: str = "llava"
    max_receipts: int = 20
    max_file_size_mb: int = 10
    temp_dir: Path = PROJECT_ROOT / "temp"
    ai_timeout_seconds: int = 60
    telegram_timeout_seconds: int = 30
    session_ttl_seconds: int = 1800
    log_level: str = "INFO"
    report_title: str = "Heading Travel Expenses"
    report_period: str = ""

    @field_validator("ai_provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        v = (v or "openai").strip().lower()
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported AI provider '{v}'. Choose one of {sorted(SUPPORTED_PROVIDERS)}"
            )
        return v

    @field_validator("allowed_user_ids", "allowed_chat_ids", mode="before")
    @classmethod
    def _parse_id_list(cls, v: Any) -> list[int]:
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        if isinstance(v, str):
            if not v.strip():
                return []
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return [int(p) for p in parts]
        raise ValueError("allowed ids must be a comma-separated string or a list of ints")

    @field_validator("max_receipts", "max_file_size_mb", "ai_timeout_seconds",
                     "telegram_timeout_seconds", "session_ttl_seconds")
    @classmethod
    def _check_positive_int(cls, v: int, info: Any) -> int:
        if v <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return v

    @field_validator("temp_dir", mode="before")
    @classmethod
    def _coerce_path(cls, v: Any) -> Any:
        if v is None or v == "":
            return Path(PROJECT_ROOT / "temp")
        return v

    @field_validator("openai_model", "ollama_model")
    @classmethod
    def _nonempty_model(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("log_level")
    @classmethod
    def _upper_log(cls, v: str) -> str:
        return (v or "INFO").strip().upper()

    def requires_telegram(self) -> bool:
        return bool(self.telegram_token)

    def validate_operational(self) -> "Config":
        """Run checks needed before the bot can actually process receipts.

        Credentials are optional at parse time so config can be constructed and
        tested with defaults; this method enforces the provider-specific key
        requirement only when the app is about to run.
        """
        if self.ai_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        if self.ai_provider == "ollama" and not self.ollama_base_url:
            raise ValueError("OLLAMA_BASE_URL is required when AI_PROVIDER=ollama")
        return self


def _from_env() -> dict[str, Any]:
    """Map raw environment values (already loaded from .env) into Config kwargs."""
    raw = os.environ

    def get(key: str, default: Any = None) -> Any:
        val = raw.get(key)
        return default if val is None or val == "" else val

    return {
        "telegram_token": get("TELEGRAM_TOKEN", ""),
        "allowed_user_ids": get("ALLOWED_USER_IDS", ""),
        "allowed_chat_ids": get("ALLOWED_CHAT_IDS", ""),
        "bot_password": get("BOT_PASSWORD", ""),
        "ai_provider": get("AI_PROVIDER", "openai"),
        "openai_api_key": get("OPENAI_API_KEY", ""),
        "openai_model": get("OPENAI_MODEL", "gpt-4o-mini"),
        "ollama_base_url": get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "ollama_api_key": get("OLLAMA_API_KEY", ""),
        "ollama_model": get("OLLAMA_MODEL", "llava"),
        "max_receipts": int(get("MAX_RECEIPTS", "20")),
        "max_file_size_mb": int(get("MAX_FILE_SIZE_MB", "10")),
        "temp_dir": Path(get("TEMP_DIR", str(PROJECT_ROOT / "temp"))),
        "ai_timeout_seconds": int(get("AI_TIMEOUT_SECONDS", "60")),
        "telegram_timeout_seconds": int(get("TELEGRAM_TIMEOUT_SECONDS", "30")),
        "session_ttl_seconds": int(get("SESSION_TTL_SECONDS", "1800")),
        "log_level": get("LOG_LEVEL", "INFO"),
        "report_title": get("REPORT_TITLE", "Heading Travel Expenses"),
        "report_period": get("REPORT_PERIOD", ""),
    }


def load_config(env_file: str | Path | None = None, *, strict: bool = True) -> Config:
    """Load configuration.

    Reads a ``.env`` file (if found) into the environment, then parses the
    environment into a validated :class:`Config`.

    When ``strict`` is True, a configuration that references an unavailable
    provider or a non-empty Telegram token/password will still construct
    normally unless a hard validation rule fires; tests that only care about
    parsing use ``strict=False`` where needed.
    """
    if env_file is not None:
        from dotenv import dotenv_values

        values = dotenv_values(env_file)
        for k, v in values.items():
            os.environ.setdefault(k, v)
    try:
        cfg = Config(**_from_env())
        if strict:
            cfg.validate_operational()
        return cfg
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
