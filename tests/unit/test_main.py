"""Tests for the application wiring in main.build_application."""

from telegram.ext import Application

from app.config import Config
from app.main import build_application


def _cfg(tmp_path) -> Config:
    return Config(
        telegram_token="t",
        allowed_user_ids="111",
        bot_password="secret",
        ai_provider="openai",
        openai_api_key="k",
        temp_dir=tmp_path,
        data_dir=tmp_path,
    )


def test_build_application_constructs_and_registers_handlers(tmp_path):
    # Exercises the full PTB wiring (including filters). This guards against
    # runtime-only breakage (e.g. an invalid filter name) that unit tests of the
    # handlers never reach because they construct ReimbursementBot directly.
    app, bot = build_application(_cfg(tmp_path))
    assert isinstance(app, Application)
    assert bot is not None
    # MessageHandler + 6 command handlers registered.
    assert len(app.handlers) == 1
    assert len(app.handlers[0]) == 7
