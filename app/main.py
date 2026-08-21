"""Application entrypoint (Telegram long polling)."""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.ai.ollama_provider import build_provider
from app.bot.bot import ReimbursementBot
from app.config import PROJECT_ROOT, Config, ConfigError, load_config
from app.services.cleanup_service import sweep_orphaned_requests
from app.services.ledger_service import ReceiptLedger
from app.services.receipt_service import ProcessingService
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)


async def _maintenance_loop(sweeper, interval_seconds: float) -> None:
    """Periodically run the session maintenance sweep (reclaim + purge)."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await sweeper()
            if result.get("reclaimed") or result.get("purged"):
                logger.info("maintenance sweep: %s", result)
        except Exception:
            logger.exception("maintenance sweep failed")


def _make_post_init(sessions: SessionStore, interval_seconds: float):
    async def _post_init(application) -> None:
        application.create_task(_maintenance_loop(sessions.sweep, interval_seconds))
    return _post_init


def build_application(
    config: Config,
    sessions: SessionStore | None = None,
    ledger: ReceiptLedger | None = None,
) -> Application:
    """Assemble the PTB application from a validated config."""
    security = SecurityService(config)
    if sessions is None:
        sessions = SessionStore(
            db_path=config.data_dir / "sessions.db",
            ttl_seconds=config.session_ttl_seconds,
            lease_ttl_seconds=config.session_lease_ttl_seconds,
        )
    if ledger is None:
        ledger = ReceiptLedger(config.data_dir / "receipts.db")

    application = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .connect_timeout(config.telegram_timeout_seconds)
        .pool_timeout(config.telegram_timeout_seconds)
        .read_timeout(config.telegram_timeout_seconds)
        .write_timeout(config.telegram_timeout_seconds)
        .build()
    )
    telegram = TelegramService(
        application.bot,
        timeout=config.telegram_timeout_seconds,
        max_file_size_mb=config.max_file_size_mb,
    )
    provider_impl = build_provider(config)
    processing = ProcessingService(config, provider_impl, telegram, ledger=ledger)
    bot = ReimbursementBot(config, security, sessions, telegram, provider_impl, processing)

    app = application
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("status", bot.status_command))
    app.add_handler(CommandHandler("clear", bot.clear_command))
    app.add_handler(CommandHandler("cancel", bot.cancel_command))
    app.add_handler(CommandHandler("generate", bot.generate_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.TEXT, bot.message_handler))
    return app


def main() -> None:
    # Load the local .env (never commit it). Values are read into the env and
    # then parsed by load_config().
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        config = load_config()
        config.validate_operational()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    # A hard kill (SIGKILL/OOM) can leave orphaned request dirs behind from a
    # previous run; sweep them before polling so the temp filesystem never fills.
    swept = sweep_orphaned_requests(config.temp_dir)
    if swept:
        logger.warning("swept %d orphaned request dirs from %s", swept, config.temp_dir)

    # Durable session store; purge stale sessions (and any stale processing
    # lease left by a crashed generation) before polling.
    sessions = SessionStore(
        db_path=config.data_dir / "sessions.db",
        ttl_seconds=config.session_ttl_seconds,
        lease_ttl_seconds=config.session_lease_ttl_seconds,
    )
    purged = asyncio.run(sessions.purge_expired())
    if purged:
        logger.warning("purged %d expired sessions", purged)

    # Durable backup of the audit + session DBs before we start serving.
    ledger = ReceiptLedger(config.data_dir / "receipts.db")
    try:
        ledger.backup(config.backup_dir)
        sessions.backup(config.backup_dir)
        logger.info("backed up state DBs to %s", config.backup_dir)
    except FileNotFoundError as exc:
        logger.warning("backup skipped: %s", exc)

    application = build_application(config, sessions=sessions, ledger=ledger)
    application.post_init = _make_post_init(sessions, config.maintenance_interval_seconds)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
