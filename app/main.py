"""Application entrypoint (Telegram long polling)."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.ai.ollama_provider import build_provider
from app.bot.bot import ReimbursementBot
from app.config import PROJECT_ROOT, Config, ConfigError, load_config
from app.services.cleanup_service import sweep_orphaned_requests
from app.services.health_server import create_health_server
from app.services.ledger_service import ReceiptLedger
from app.services.receipt_service import ProcessingService
from app.services.security_service import SecurityService
from app.services.session_service import SessionStore
from app.services.telegram_service import TelegramService
from app.utils.singleton import InstanceLock

logger = logging.getLogger(__name__)


async def _maintenance_loop(
    sweeper,
    interval_seconds: float,
    evict_idle=None,
    lock_idle_seconds: float = 0.0,
) -> None:
    """Periodically run the session maintenance sweep (reclaim + purge) and
    evict idle per-user locks so their memory does not grow unboundedly."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await sweeper()
            if result.get("reclaimed") or result.get("purged"):
                logger.info(
                    "maintenance sweep: reclaimed=%(reclaimed)d purged=%(purged)d",
                    result,
                )
        except Exception:
            logger.exception("maintenance sweep failed")
        if evict_idle is not None:
            try:
                evicted = evict_idle(lock_idle_seconds)
                if evicted:
                    logger.info("evicted %d idle user locks", evicted)
            except Exception:
                logger.exception("lock eviction failed")


def _make_post_init(
    sessions: SessionStore,
    interval_seconds: float,
    locks=None,
    lock_idle_seconds: float = 0.0,
):
    """Return a ``post_init`` that starts the maintenance task and tracks it.

    PTB's ``Application`` uses ``__slots__``, so the task reference is held in a
    closure (never attached to the app object). ``post_shutdown`` cancels and
    awaits the task so it is reaped cleanly at shutdown instead of leaking.
    """
    evict_idle = locks.evict_idle if locks is not None else None
    holder: dict = {"task": None}

    async def _post_shutdown(_app) -> None:
        task = holder["task"]
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _post_init(application) -> None:
        application.post_shutdown = _post_shutdown
        holder["task"] = asyncio.get_running_loop().create_task(
            _maintenance_loop(
                sessions.sweep,
                interval_seconds,
                evict_idle,
                lock_idle_seconds,
            )
        )

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
    app.post_init = _make_post_init(
        sessions,
        config.maintenance_interval_seconds,
        bot.locks,
        config.session_lease_ttl_seconds,
    )
    return app


def _start_health_server(config: Config) -> None:
    """Serve ``/health`` and ``/metrics`` in a daemon thread if enabled."""
    if not config.health_enabled:
        return
    server = create_health_server(port=config.health_port, token=config.health_token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("health server listening on :%d", config.health_port)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        config = load_config()
        config.validate_operational()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    # Resolve dirs to absolute paths, creating + verifying them writable up front.
    config.temp_dir = Path(config.temp_dir).resolve()
    config.data_dir = Path(config.data_dir).resolve()
    config.backup_dir = Path(config.backup_dir).resolve()
    for d in (config.temp_dir, config.data_dir, config.backup_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
            if not os.access(d, os.W_OK):
                raise SystemExit(f"Directory not writable: {d}")
        except PermissionError as exc:
            raise SystemExit(
                f"Cannot create/write directory {d}: {exc}. "
                "Check the runtime user owns the mount (TEMP_DIR/DATA_DIR/BACKUP_DIR)."
            ) from exc

    # Single-instance guard. The lock file sits on the shared data volume, so
    # a duplicate container sharing that volume competes for the same lock.
    # Take it before touching any shared state (orphan sweep, session purge,
    # backup) so a second instance fails loudly instead of 409-conflicting on
    # getUpdates and silently dropping updates.
    instance_lock = InstanceLock(config.data_dir / "instance.lock")
    if not instance_lock.acquire():
        logger.error(
            "another bot instance is already running (lock held on %s); "
            "refusing to start a second poller",
            instance_lock.path,
        )
        raise SystemExit(1)

    # Sweep orphaned request dirs from a previous hard kill (SIGKILL/OOM).
    logger.info(
        "config: concurrency=%d request_delay=%.1fs retries=%d retry_delay=%.1fs "
        "max_edge=%d TPM-aware_retry=yes",
        config.ai_concurrency,
        config.ai_request_delay_seconds,
        config.ai_retry_attempts,
        config.ai_retry_base_delay,
        config.image_max_edge,
    )
    swept = sweep_orphaned_requests(config.temp_dir)
    if swept:
        logger.warning("swept %d orphaned request dirs from %s", swept, config.temp_dir)

    # Durable session store; purge stale sessions and abandoned leases first.
    sessions = SessionStore(
        db_path=config.data_dir / "sessions.db",
        ttl_seconds=config.session_ttl_seconds,
        lease_ttl_seconds=config.session_lease_ttl_seconds,
    )
    purged = asyncio.run(sessions.purge_expired())
    if purged:
        logger.warning("purged %d expired sessions", purged)

    # Durable backup of the audit + session DBs before serving.
    ledger = ReceiptLedger(config.data_dir / "receipts.db")
    try:
        ledger.backup(config.backup_dir, retention=config.backup_retention)
        sessions.backup(config.backup_dir, retention=config.backup_retention)
        logger.info("backed up state DBs to %s", config.backup_dir)
    except FileNotFoundError as exc:
        logger.warning("backup skipped: %s", exc)

    application = build_application(config, sessions=sessions, ledger=ledger)
    _start_health_server(config)
    try:
        application.run_polling(drop_pending_updates=True)
    finally:
        # Release the lock on clean shutdown; if we were SIGKILLed/OOM'd the
        # kernel already released it via fd close.
        instance_lock.release()


if __name__ == "__main__":
    main()
