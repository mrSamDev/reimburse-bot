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
    assert cfg.worker_count == 2


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
    assert SUPPORTED_PROVIDERS == {"openai", "ollama", "pool"}


def test_pool_requires_both_keys():
    with pytest.raises(ValueError):
        Config(ai_provider="pool", openai_api_key="k", ollama_base_url="").validate_operational()
    with pytest.raises(ValueError):
        Config(ai_provider="pool", openai_api_key="", ollama_base_url="http://x").validate_operational()


def test_pool_with_both_keys_passes():
    cfg = Config(ai_provider="pool", openai_api_key="k", ollama_base_url="http://x")
    assert cfg.validate_operational() is cfg


def test_pool_strategy_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_POOL_STRATEGY", raising=False)
    assert load_config(strict=False).ai_pool_strategy == "round_robin"

    env = tmp_path / ".env"
    env.write_text("AI_POOL_STRATEGY=priority\n")
    assert load_config(env_file=env, strict=False).ai_pool_strategy == "priority"


def test_pool_strategy_invalid_rejected():
    with pytest.raises(ValueError):
        Config(ai_pool_strategy="random")


def test_pool_primary_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_POOL_PRIMARY", raising=False)
    assert load_config(strict=False).ai_pool_primary == "ollama"

    env = tmp_path / ".env"
    env.write_text("AI_POOL_PRIMARY=openai\n")
    assert load_config(env_file=env, strict=False).ai_pool_primary == "openai"


def test_pool_primary_invalid_rejected():
    with pytest.raises(ValueError):
        Config(ai_pool_primary="claude")


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


def test_backup_retention_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKUP_RETENTION", raising=False)
    cfg = load_config(strict=False)
    assert cfg.backup_retention == 10

    env = tmp_path / ".env"
    env.write_text("BACKUP_RETENTION=3\n")
    cfg = load_config(env_file=env, strict=False)
    assert cfg.backup_retention == 3


def test_backup_retention_must_be_positive(monkeypatch):
    _clear_env()
    monkeypatch.setenv("BACKUP_RETENTION", "0")
    with pytest.raises((ConfigError, ValueError)):
        load_config(strict=False)


def test_ai_max_calls_per_run_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_MAX_CALLS_PER_RUN", raising=False)
    assert load_config(strict=False).ai_max_calls_per_run == 100

    env = tmp_path / ".env"
    env.write_text("AI_MAX_CALLS_PER_RUN=3\n")
    assert load_config(env_file=env, strict=False).ai_max_calls_per_run == 3


def test_ai_max_calls_per_run_must_be_positive(monkeypatch):
    _clear_env()
    monkeypatch.setenv("AI_MAX_CALLS_PER_RUN", "0")
    with pytest.raises((ConfigError, ValueError)):
        load_config(strict=False)


def test_worker_count_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKER_COUNT", raising=False)
    assert load_config(strict=False).worker_count == 2

    env = tmp_path / ".env"
    env.write_text("WORKER_COUNT=4\n")
    assert load_config(env_file=env, strict=False).worker_count == 4


def test_worker_count_must_be_positive(monkeypatch):
    _clear_env()
    monkeypatch.setenv("WORKER_COUNT", "0")
    with pytest.raises((ConfigError, ValueError)):
        load_config(strict=False)


def test_max_queue_size_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MAX_QUEUE_SIZE", raising=False)
    assert load_config(strict=False).max_queue_size == 50

    env = tmp_path / ".env"
    env.write_text("MAX_QUEUE_SIZE=5\n")
    assert load_config(env_file=env, strict=False).max_queue_size == 5


def test_max_queue_size_must_be_positive(monkeypatch):
    _clear_env()
    monkeypatch.setenv("MAX_QUEUE_SIZE", "0")
    with pytest.raises((ConfigError, ValueError)):
        load_config(strict=False)


def test_health_token_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HEALTH_TOKEN", raising=False)
    assert load_config(strict=False).health_token == ""

    env_file = tmp_path / "envfile"
    env_file.write_text("HEALTH_TOKEN=probe-secret\n")
    assert load_config(env_file=env_file, strict=False).health_token == "probe-secret"


def test_password_throttle_config_default_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSWORD_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("PASSWORD_LOCKOUT_SECONDS", raising=False)
    cfg = load_config(strict=False)
    assert cfg.password_max_attempts == 5
    assert cfg.password_lockout_seconds == 300

    env_file = tmp_path / "envfile"
    env_file.write_text("PASSWORD_MAX_ATTEMPTS=3\nPASSWORD_LOCKOUT_SECONDS=60\n")
    cfg = load_config(env_file=env_file, strict=False)
    assert cfg.password_max_attempts == 3
    assert cfg.password_lockout_seconds == 60

    monkeypatch.setenv("PASSWORD_MAX_ATTEMPTS", "0")
    with pytest.raises((ConfigError, ValueError)):
        load_config(strict=False)
