"""Tests for the application wiring in main.build_application."""

import pytest
from telegram.ext import Application

from app.config import Config
from app.main import _bootstrap, _start_health_server, build_application
from app.services.ledger_service import ReceiptLedger
from app.services.session_service import SessionStore
from app.utils.singleton import InstanceLock


def _cfg(tmp_path) -> Config:
    return Config(
        ai_provider="openai",
        openai_api_key="k",
        temp_dir=tmp_path / "temp",
        data_dir=tmp_path / "data",
        backup_dir=tmp_path / "backups",
    )


class _FakeServer:
    """Minimal stand-in for a ThreadingHTTPServer."""

    def __init__(self):
        self.serving = False

    def serve_forever(self):
        self.serving = True


class _FakeThread:
    """Capture construction args and record start()/daemon flag."""

    instances = []

    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon
        self.started = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True


def _clear_fake_threads():
    _FakeThread.instances.clear()


def test_start_health_server_disabled_does_nothing(monkeypatch):
    def _should_not_call(**kw):
        raise AssertionError("create_health_server must not run when disabled")

    monkeypatch.setattr("app.main.create_health_server", _should_not_call)
    _start_health_server(Config(ai_provider="openai", openai_api_key="k", health_enabled=False))


def test_start_health_server_enabled_starts_daemon_thread(monkeypatch):
    _clear_fake_threads()
    server = _FakeServer()
    monkeypatch.setattr("app.main.create_health_server", lambda **kw: server)
    monkeypatch.setattr("app.main.threading.Thread", _FakeThread)

    _start_health_server(Config(ai_provider="openai", openai_api_key="k", health_enabled=True))

    # One daemon thread was built with serve_forever as its target and started.
    assert len(_FakeThread.instances) == 1
    thread = _FakeThread.instances[0]
    assert thread.daemon is True
    assert thread.started is True
    # serve_forever (bound to the fake server) is the thread target.\n    assert getattr(thread.target, \"__self__\", None) is server



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


def test_bootstrap_creates_dirs_and_returns_stores(tmp_path):
    cfg = _cfg(tmp_path)
    sessions, ledger, lock = _bootstrap(cfg)

    assert isinstance(sessions, SessionStore)
    assert isinstance(ledger, ReceiptLedger)
    assert isinstance(lock, InstanceLock)
    # All three runtime dirs exist and the lock is held.
    for d in (cfg.temp_dir, cfg.data_dir, cfg.backup_dir):
        assert d.is_dir()
    # A real lock file exists and is acquired.
    assert (cfg.data_dir / "instance.lock").exists()
    lock.release()


def test_bootstrap_non_writable_dir_raises_systemexit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("app.main.os.access", lambda *a, **k: False)
    with pytest.raises(SystemExit, match="Directory not writable"):
        _bootstrap(cfg)


def test_bootstrap_permission_error_raises_systemexit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def _raise_permission(*a, **k):
        raise PermissionError("no perms")

    monkeypatch.setattr("app.main.Path.mkdir", _raise_permission)
    with pytest.raises(SystemExit, match="Cannot create/write directory"):
        _bootstrap(cfg)


def test_bootstrap_lock_held_raises_systemexit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("app.main.InstanceLock.acquire", lambda self: False)
    with pytest.raises(SystemExit) as excinfo:
        _bootstrap(cfg)
    assert excinfo.value.code == 1


def test_bootstrap_backup_failure_is_nonfatal(tmp_path, monkeypatch, caplog):
    cfg = _cfg(tmp_path)

    def _raise_not_found(*a, **k):
        raise FileNotFoundError("db vanished")

    monkeypatch.setattr("app.main.ReceiptLedger.backup", _raise_not_found)
    # Must not raise: backup failure is logged and startup continues.
    sessions, ledger, lock = _bootstrap(cfg)
    assert any("backup skipped" in r.message for r in caplog.records)
    lock.release()


class _FakeApplication:
    """Minimal stand-in exposing run_polling for _run_polling tests."""

    def __init__(self, behavior="return"):
        self._behavior = behavior
        self.run_polling_calls = 0
        self.drop_pending = None

    def run_polling(self, **kwargs):
        self.run_polling_calls += 1
        self.drop_pending = kwargs.get("drop_pending_updates")
        if self._behavior == "raise":
            raise RuntimeError("polling failed")
        # Normal: return to simulate graceful shutdown.


class _FakeLock:
    """Minimal stand-in for InstanceLock recording release() calls."""

    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


def test_run_polling_releases_lock_on_shutdown(monkeypatch):
    from app.main import _run_polling

    app = _FakeApplication()
    lock = _FakeLock()
    _run_polling(app, lock)
    assert app.run_polling_calls == 1
    assert app.drop_pending is True
    assert lock.released == 1


def test_run_polling_releases_lock_on_exception(monkeypatch):
    from app.main import _run_polling

    app = _FakeApplication(behavior="raise")
    lock = _FakeLock()
    with pytest.raises(RuntimeError, match="polling failed"):
        _run_polling(app, lock)
    # Lock must still be released even when polling raises.
    assert lock.released == 1
