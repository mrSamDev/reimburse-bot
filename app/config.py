"""Application configuration loaded from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_PROVIDERS = {"openai", "ollama", "pool"}
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
    ai_pool_strategy: str = "round_robin"
    ai_pool_primary: str = "ollama"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_api_key: str = ""
    ollama_model: str = "llava"
    max_receipts: int = 20
    max_file_size_mb: int = 10
    image_max_edge: int = 1024
    temp_dir: Path = PROJECT_ROOT / "temp"
    data_dir: Path = PROJECT_ROOT / "data"
    backup_dir: Path = PROJECT_ROOT / "backups"
    backup_retention: int = 10
    ai_timeout_seconds: int = 60
    ai_retry_attempts: int = 3
    ai_retry_base_delay: float = 1.0
    ai_request_delay_seconds: float = 1.0
    ai_concurrency: int = 1
    ai_per_receipt_timeout_seconds: int = 120
    ai_max_calls_per_run: int = 100
    worker_count: int = 2
    max_processing_seconds: float = 600.0
    telegram_timeout_seconds: int = 30
    session_ttl_seconds: int = 1800
    session_lease_ttl_seconds: int = 120
    maintenance_interval_seconds: int = 60
    log_level: str = "INFO"
    log_format: str = "text"
    health_enabled: bool = False
    health_port: int = 8080
    health_token: str = ""
    password_max_attempts: int = 5
    password_lockout_seconds: int = 300
    report_title: str = "Heading Travel Expenses"
    # Deprecated: the report period is now derived from the receipts'
    # transaction dates (see app/services/report_period.py), not read from env.
    # Kept only so existing call sites that construct Config(report_period=...) work.
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

    @field_validator("ai_pool_strategy")
    @classmethod
    def _check_pool_strategy(cls, v: str) -> str:
        v = (v or "round_robin").strip().lower()
        if v not in {"round_robin", "priority"}:
            raise ValueError("ai_pool_strategy must be 'round_robin' or 'priority'")
        return v

    @field_validator("ai_pool_primary")
    @classmethod
    def _check_pool_primary(cls, v: str) -> str:
        v = (v or "ollama").strip().lower()
        if v not in {"openai", "ollama"}:
            raise ValueError("ai_pool_primary must be 'openai' or 'ollama'")
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
                     "telegram_timeout_seconds", "session_ttl_seconds",
                     "password_max_attempts", "password_lockout_seconds")
    @classmethod
    def _check_positive_int(cls, v: int, info: Any) -> int:
        if v <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return v

    @field_validator("image_max_edge")
    @classmethod
    def _check_image_max_edge(cls, v: int) -> int:
        if v < 256:
            raise ValueError("image_max_edge must be >= 256")
        return v

    @field_validator("ai_retry_attempts")
    @classmethod
    def _check_retry_attempts(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ai_retry_attempts must be >= 1")
        return v

    @field_validator("ai_retry_base_delay")
    @classmethod
    def _check_retry_delay(cls, v: float) -> float:
        if v < 0:
            raise ValueError("ai_retry_base_delay must be >= 0")
        return v

    @field_validator("ai_request_delay_seconds")
    @classmethod
    def _check_request_delay(cls, v: float) -> float:
        if v < 0:
            raise ValueError("ai_request_delay_seconds must be >= 0")
        return v

    @field_validator("ai_concurrency")
    @classmethod
    def _check_concurrency(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ai_concurrency must be >= 1")
        return v

    @field_validator("worker_count")
    @classmethod
    def _check_worker_count(cls, v: int) -> int:
        if v < 1:
            raise ValueError("worker_count must be >= 1")
        return v

    @field_validator("session_lease_ttl_seconds")
    @classmethod
    def _check_lease_ttl(cls, v: int) -> int:
        if v < 1:
            raise ValueError("session_lease_ttl_seconds must be >= 1")
        return v

    @field_validator("log_format")
    @classmethod
    def _check_log_format(cls, v: str) -> str:
        v = (v or "text").strip().lower()
        if v not in {"text", "json"}:
            raise ValueError("log_format must be 'text' or 'json'")
        return v

    @field_validator("ai_per_receipt_timeout_seconds")
    @classmethod
    def _check_per_receipt_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ai_per_receipt_timeout_seconds must be >= 1")
        return v

    @field_validator("ai_max_calls_per_run")
    @classmethod
    def _check_ai_max_calls(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ai_max_calls_per_run must be >= 1")
        return v

    @field_validator("health_port")
    @classmethod
    def _check_health_port(cls, v: int) -> int:
        if not (0 <= v <= 65535):
            raise ValueError("health_port must be a valid TCP port")
        return v

    @field_validator("maintenance_interval_seconds")
    @classmethod
    def _check_maintenance_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("maintenance_interval_seconds must be >= 1")
        return v

    @field_validator("backup_retention")
    @classmethod
    def _check_backup_retention(cls, v: int) -> int:
        if v < 1:
            raise ValueError("backup_retention must be >= 1")
        return v

    @field_validator("max_processing_seconds")
    @classmethod
    def _check_budget(cls, v: float) -> float:
        if v < 0:
            raise ValueError("max_processing_seconds must be >= 0")
        return v

    @field_validator("temp_dir", "data_dir", "backup_dir", mode="before")
    @classmethod
    def _coerce_path(cls, v: Any, info: Any) -> Path:
        if v is None or v == "":
            default = PROJECT_ROOT / {
                "data_dir": "data",
                "backup_dir": "backups",
            }.get(info.field_name, "temp")
            return Path(default)
        return Path(v)

    @field_validator("openai_model", "ollama_model")
    @classmethod
    def _nonempty_model(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("log_level")
    @classmethod
    def _upper_log(cls, v: str) -> str:
        return (v or "INFO").strip().upper()

    def validate_operational(self) -> Config:
        """Run checks needed before the bot can actually process receipts.

        Credentials are optional at parse time so config can be constructed and
        tested with defaults; this method enforces the provider-specific key
        requirement only when the app is about to run.
        """
        if self.ai_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        if self.ai_provider == "ollama" and not self.ollama_base_url:
            raise ValueError("OLLAMA_BASE_URL is required when AI_PROVIDER=ollama")
        if self.ai_provider == "pool":
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required when AI_PROVIDER=pool")
            if not self.ollama_base_url:
                raise ValueError("OLLAMA_BASE_URL is required when AI_PROVIDER=pool")
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
        "ai_pool_strategy": get("AI_POOL_STRATEGY", "round_robin"),
        "ai_pool_primary": get("AI_POOL_PRIMARY", "ollama"),
        "openai_api_key": get("OPENAI_API_KEY", ""),
        "openai_model": get("OPENAI_MODEL", "gpt-4o-mini"),
        "ollama_base_url": get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "ollama_api_key": get("OLLAMA_API_KEY", ""),
        "ollama_model": get("OLLAMA_MODEL", "llava"),
        "max_receipts": int(get("MAX_RECEIPTS", "20")),
        "max_file_size_mb": int(get("MAX_FILE_SIZE_MB", "10")),
        "image_max_edge": int(get("IMAGE_MAX_EDGE", "1024")),
        "temp_dir": Path(get("TEMP_DIR", str(PROJECT_ROOT / "temp"))),
        "data_dir": Path(get("DATA_DIR", str(PROJECT_ROOT / "data"))),
        "backup_dir": Path(get("BACKUP_DIR", str(PROJECT_ROOT / "backups"))),
        "backup_retention": int(get("BACKUP_RETENTION", "10")),
        "ai_timeout_seconds": int(get("AI_TIMEOUT_SECONDS", "60")),
        "ai_retry_attempts": int(get("AI_RETRY_ATTEMPTS", "3")),
        "ai_retry_base_delay": float(get("AI_RETRY_BASE_DELAY", "1.0")),
        "ai_request_delay_seconds": float(get("AI_REQUEST_DELAY_SECONDS", "1.0")),
        "ai_concurrency": int(get("AI_CONCURRENCY", "1")),
        "ai_per_receipt_timeout_seconds": int(get("AI_PER_RECEIPT_TIMEOUT_SECONDS", "120")),
        "ai_max_calls_per_run": int(get("AI_MAX_CALLS_PER_RUN", "100")),
        "worker_count": int(get("WORKER_COUNT", "2")),
        "max_processing_seconds": float(get("MAX_PROCESSING_SECONDS", "600")),
        "telegram_timeout_seconds": int(get("TELEGRAM_TIMEOUT_SECONDS", "30")),
        "session_ttl_seconds": int(get("SESSION_TTL_SECONDS", "1800")),
        "session_lease_ttl_seconds": int(get("SESSION_LEASE_TTL_SECONDS", "120")),
        "maintenance_interval_seconds": int(get("MAINTENANCE_INTERVAL_SECONDS", "60")),
        "log_level": get("LOG_LEVEL", "INFO"),
        "log_format": get("LOG_FORMAT", "text"),
        "health_enabled": (get("HEALTH_ENABLED", "false") or "false").lower() in ("1", "true", "yes"),
        "health_port": int(get("HEALTH_PORT", "8080")),
        "health_token": get("HEALTH_TOKEN", ""),
        "password_max_attempts": int(get("PASSWORD_MAX_ATTEMPTS", "5")),
        "password_lockout_seconds": int(get("PASSWORD_LOCKOUT_SECONDS", "300")),
        "report_title": get("REPORT_TITLE", "Heading Travel Expenses"),
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
            if v is not None:
                os.environ.setdefault(k, v)
    try:
        cfg = Config(**_from_env())
        if strict:
            cfg.validate_operational()
        return cfg
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
