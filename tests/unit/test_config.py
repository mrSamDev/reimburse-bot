"""Tests for app.config."""

import os
from pathlib import Path

import pytest

from app.config import (
    SUPPORTED_PROVIDERS,
    Config,
    ConfigError,
    load_config,
)


def _clear_env():
    for k in list(os.environ):
        if k.startswith(("TELEGRAM_", "ALLOWED_", "BOT_", "AI_", "OPENAI_", "OLLAMA_",
                         "MAX_", "TEMP_", "LOG_", "REPORT_", "SESSION_", "PROVIDER")):
            os.environ.pop(k, None)


@pytest.fixture(autouse=True)
def _clean_env():
    _clear_env()
    yield
    _clear_env()


def test_defaults():
    cfg = Config()
    assert cfg.ai_provider == "openai"
    assert cfg.max_receipts == 20
    assert cfg.max_file_size_mb == 10
    assert cfg.ai_timeout_seconds == 60
    assert cfg.telegram_timeout_seconds == 30
    assert cfg.allowed_user_ids == []
    assert cfg.report_title == "Heading Travel Expenses"
    assert cfg.ai_concurrency == 1
    assert cfg.ai_request_delay_seconds == 1.0
    assert cfg.image_max_edge == 1024


def test_default_provider_openai_requires_key_at_runtime():
    cfg = Config()
    with pytest.raises(ValueError):
        cfg.validate_operational()


def test_required_variable_openai_key():
    cfg = Config(ai_provider="openai")
    with pytest.raises(ValueError):
        cfg.validate_operational()


def test_ollama_requires_base_url():
    cfg = Config(ai_provider="ollama", ollama_base_url="")
    with pytest.raises(ValueError):
        cfg.validate_operational()


def test_ollama_with_base_url_passes():
    cfg = Config(ai_provider="ollama", ollama_base_url="http://x")
    assert cfg.validate_operational() is cfg


def test_unsupported_provider_rejected():
    with pytest.raises(ValueError):
        Config(ai_provider="claude")


def test_supported_providers_enum():
    assert SUPPORTED_PROVIDERS == {"openai", "ollama"}


def test_allowed_user_ids_parsing_comma_string():
    cfg = Config(allowed_user_ids="123456789, 987654321")
    assert cfg.allowed_user_ids == [123456789, 987654321]


def test_allowed_user_ids_empty_string():
    assert Config(allowed_user_ids="").allowed_user_ids == []


def test_allowed_user_ids_invalid():
    with pytest.raises(ValueError):
        Config(allowed_user_ids="123,abc")


def test_chat_ids_parsing():
    assert Config(allowed_chat_ids="10,20").allowed_chat_ids == [10, 20]


def test_numeric_config_validation():
    with pytest.raises(ValueError):
        Config(max_receipts=0)
    with pytest.raises(ValueError):
        Config(max_file_size_mb=-5)


def test_temp_dir_coerced_to_path():
    assert isinstance(Config().temp_dir, Path)


def test_load_config_defaults():
    cfg = load_config(strict=False)
    assert cfg.max_receipts == 20


def test_load_config_missing_telegram_token_ok_loose():
    cfg = load_config(strict=False)
    assert cfg.telegram_token == ""


def test_invalid_config_raises_configerror():
    _clear_env()
    os.environ["MAX_RECEIPTS"] = "notanumber"
    with pytest.raises((ConfigError, ValueError)):
        load_config(strict=False)


def test_env_file_loading(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ALLOWED_USER_IDS=1,2,3\nMAX_RECEIPTS=5\n")
    cfg = load_config(env_file=env, strict=False)
    assert cfg.allowed_user_ids == [1, 2, 3]
    assert cfg.max_receipts == 5
