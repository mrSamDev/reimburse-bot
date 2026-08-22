"""Tests for the Session model and SQLite-backed SessionStore."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.bot.states import BotState
from app.models.session import Session
from app.services.session_service import SessionStore


def _db(tmp_path):
    return tmp_path / "sessions.db"


def _store(tmp_path, **kw):
    return SessionStore(_db(tmp_path), **kw)


# ---- Session model --------------------------------------------------------

def test_session_defaults():
    s = Session(user_id=1, chat_id=1)
    assert s.state == BotState.IDLE
    assert s.receipt_file_ids == []


def test_add_file_id():
    s = Session(user_id=1, chat_id=1)
    assert s.add_file_id("f1") is True
    assert s.add_file_id("f2") is True
    assert s.receipt_file_ids == ["f1", "f2"]


def test_duplicate_file_id_not_added():
    s = Session(user_id=1, chat_id=1)
    assert s.add_file_id("f1") is True
    assert s.add_file_id("f1") is False
    assert s.receipt_file_ids == ["f1"]


def test_clear_receipts():
    s = Session(user_id=1, chat_id=1)
    s.add_file_id("f1")
    s.clear_receipts()
    assert s.receipt_file_ids == []


def test_touch_updates_updated_at():
    s = Session(user_id=1, chat_id=1)
    before = s.updated_at
    s.add_file_id("f1")
    assert s.updated_at >= before


# ---- Store persistence (repository-save semantics) -----------------------

async def test_save_and_get_round_trip(tmp_path):
    store = _store(tmp_path)
    s = await store.get(5)
    s.chat_id = 999
    s.add_file_id("f1")
    s.state = BotState.COLLECTING
    await store.save(s)
    loaded = await store.get(5)
    assert loaded.user_id == 5
    assert loaded.chat_id == 999
    assert loaded.receipt_file_ids == ["f1"]
    assert loaded.state == BotState.COLLECTING


async def test_get_returns_detached_copy(tmp_path):
    # Repository semantics: get() returns a snapshot; mutations need save().
    store = _store(tmp_path)
    a = await store.get(5)
    a.add_file_id("f1")
    # The un-saved mutation must not be visible to a fresh read.
    b = await store.get(5)
    assert b.receipt_file_ids == []


# ---- Atomic list mutations (cross-process lost-update fix) -------------

async def test_add_file_id_atomic_creates_row(tmp_path):
    store = _store(tmp_path)
    assert await store.add_file_id(1, "f1") is True
    assert (await store.get(1)).receipt_file_ids == ["f1"]


async def test_add_file_id_dedupe_returns_false(tmp_path):
    store = _store(tmp_path)
    assert await store.add_file_id(1, "f1") is True
    assert await store.add_file_id(1, "f1") is False
    assert (await store.get(1)).receipt_file_ids == ["f1"]


async def test_add_file_id_cross_instance_both_persist(tmp_path):
    """Two instances appending different file ids to the same user must both
    persist (the historical get->mutate->upsert lost one of them)."""
    a = _store(tmp_path)
    b = _store(tmp_path)
    assert await a.add_file_id(1, "f1") is True
    assert await b.add_file_id(1, "f2") is True
    assert (await a.get(1)).receipt_file_ids == ["f1", "f2"]


async def test_save_does_not_clobber_concurrent_append(tmp_path):
    """Regression: a full-snapshot save() must not overwrite receipt_file_ids
    that another instance appended after this snapshot was read."""
    a = _store(tmp_path)
    b = _store(tmp_path)
    await a.add_file_id(1, "f1")
    stale = await b.get(1)          # snapshot that only knows about f1
    await a.add_file_id(1, "f2")    # concurrent append by another instance
    stale.chat_id = 999
    await b.save(stale)             # scalar-only save must NOT clobber f2
    loaded = await a.get(1)
    assert loaded.receipt_file_ids == ["f1", "f2"]
    assert loaded.chat_id == 999    # scalar field still persisted


async def test_clear_receipts_atomic_resets_and_title(tmp_path):
    store = _store(tmp_path)
    await store.add_file_id(1, "f1")
    s = await store.get(1)
    s.report_title = "My Expenses"
    await store.save(s)
    await store.clear_receipts(1)
    loaded = await store.get(1)
    assert loaded.receipt_file_ids == []
    assert loaded.report_title == ""


async def test_isolation_between_users(tmp_path):
    store = _store(tmp_path)
    s1 = await store.get(10)
    s1.add_file_id("fA")
    await store.save(s1)
    s2 = await store.get(20)
    assert s2.receipt_file_ids == []


async def test_multiple_users_independent_receipts(tmp_path):
    store = _store(tmp_path)
    u1 = await store.get(1)
    u1.add_file_id("u1a")
    await store.save(u1)
    u2 = await store.get(2)
    u2.add_file_id("u2a")
    u2.add_file_id("u2b")
    await store.save(u2)
    assert (await store.get(1)).receipt_file_ids == ["u1a"]
    assert (await store.get(2)).receipt_file_ids == ["u2a", "u2b"]


async def test_store_clear(tmp_path):
    store = _store(tmp_path)
    s = await store.get(5)
    await store.save(s)
    assert await store.count() == 1
    await store.clear(5)
    assert await store.count() == 0


async def test_persists_across_store_instances(tmp_path):
    # Data survives a new SessionStore on the same DB (i.e. a restart).
    s1 = _store(tmp_path)
    sess = await s1.get(5)
    sess.add_file_id("f1")
    await s1.save(sess)
    s2 = _store(tmp_path)
    assert (await s2.get(5)).receipt_file_ids == ["f1"]


async def test_reset_stale_returns_to_idle(tmp_path):
    store = _store(tmp_path)
    s = await store.get(5)
    s.state = BotState.QUEUED
    await store.save(s)
    assert (await store.get(5)).state == BotState.QUEUED
    assert await store.reset_stale() == 1
    assert (await store.get(5)).state == BotState.IDLE


async def test_reset_stale_resets_queued_and_processing(tmp_path):
    store = _store(tmp_path)
    for uid, state in [
        (1, BotState.PROCESSING),
        (2, BotState.QUEUED),
        (3, BotState.COLLECTING),
        (4, BotState.IDLE),
    ]:
        s = await store.get(uid)
        s.state = state
        await store.save(s)
    assert await store.reset_stale() == 2  # PROCESSING + QUEUED
    assert (await store.get(1)).state == BotState.IDLE
    assert (await store.get(2)).state == BotState.IDLE
    assert (await store.get(3)).state == BotState.COLLECTING
    assert (await store.get(4)).state == BotState.IDLE


async def test_reset_stale_clears_processing_flag(tmp_path):
    store = _store(tmp_path)
    assert await store.try_acquire_processing(1) is True
    await store.reset_stale()
    assert await store.is_processing(1) is False


async def test_get_stale_returns_queued_and_processing(tmp_path):
    store = _store(tmp_path)
    s1 = await store.get(1)
    s1.state = BotState.QUEUED
    await store.save(s1)
    s2 = await store.get(2)
    s2.state = BotState.PROCESSING
    await store.save(s2)
    s3 = await store.get(3)
    s3.state = BotState.QUEUED
    await store.save(s3)
    stale = await store.get_stale()
    assert stale == [(1, 1), (2, 2), (3, 3)]  # (user_id, chat_id) for QUEUED + PROCESSING


async def test_get_stale_returns_processing_flag_sessions(tmp_path):
    """A crash between try_acquire_processing and the state save leaves
    state=IDLE but processing=1; get_stale must still return it so the user is
    notified their job was lost (closes the get_stale/reset_stale asymmetry)."""
    store = _store(tmp_path)
    assert await store.try_acquire_processing(1) is True
    # Simulate the crash: processing flag set, but state never advanced.
    stale = await store.get_stale()
    assert stale == [(1, 1)]


# ---- TTL ------------------------------------------------------------------

async def test_expired_session_returns_fresh(tmp_path):
    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=31)
    stale = Session(user_id=5, chat_id=5, receipt_file_ids=["old"], updated_at=past, created_at=past)
    await store.save(stale)
    fresh = await store.get(5)
    assert fresh.receipt_file_ids == []  # expired session not carried forward
    assert fresh.updated_at >= datetime.now(timezone.utc) - timedelta(seconds=1)


async def test_not_expired_within_ttl(tmp_path):
    store = _store(tmp_path, ttl_seconds=60)
    s = await store.get(5)
    s.add_file_id("f1")
    await store.save(s)
    assert (await store.get(5)).receipt_file_ids == ["f1"]


async def test_purge_expired_removes_stale(tmp_path):
    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=31)
    await store.save(Session(user_id=1, chat_id=1, updated_at=past, created_at=past))
    await store.save(Session(user_id=2, chat_id=2))
    removed = await store.purge_expired()
    assert removed == 1
    assert await store.count() == 1


async def test_purge_expired_removes_multiple_stale(tmp_path):
    """A single purge call must remove every stale session (SQL path, not a
    per-row Python loop) and report the correct count."""
    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=40)
    for uid in (1, 2, 3):
        await store.save(
            Session(user_id=uid, chat_id=uid, updated_at=past, created_at=past)
        )
    await store.save(Session(user_id=4, chat_id=4))  # fresh
    removed = await store.purge_expired()
    assert removed == 3
    assert await store.count() == 1


# ---- Cross-process processing claim --------------------------------------

async def test_try_acquire_processing_single(tmp_path):
    store = _store(tmp_path)
    assert await store.try_acquire_processing(5) is True
    assert await store.try_acquire_processing(5) is False  # already held
    await store.release_processing(5)
    assert await store.try_acquire_processing(5) is True


async def test_try_acquire_cross_process_exactly_one_wins(tmp_path):
    a = _store(tmp_path)
    b = _store(tmp_path)  # separate connection, same DB -> separate process
    assert await a.try_acquire_processing(7) is True
    assert await b.try_acquire_processing(7) is False
    await a.release_processing(7)
    assert await b.try_acquire_processing(7) is True


async def test_different_users_independent_processing(tmp_path):
    store = _store(tmp_path)
    assert await store.try_acquire_processing(1) is True
    assert await store.try_acquire_processing(2) is True
    assert await store.try_acquire_processing(1) is False


async def test_try_acquire_creates_row(tmp_path):
    store = _store(tmp_path)
    assert await store.count() == 0
    assert await store.try_acquire_processing(9) is True
    assert await store.count() == 1


async def test_release_clears_processing(tmp_path):
    store = _store(tmp_path)
    assert await store.try_acquire_processing(1) is True
    await store.release_processing(1)
    # After release the slot is re-acquirable.
    assert await store.try_acquire_processing(1) is True


async def test_is_processing_reflects_flag(tmp_path):
    store = _store(tmp_path)
    assert await store.is_processing(1) is False
    await store.try_acquire_processing(1)
    assert await store.is_processing(1) is True
    await store.release_processing(1)
    assert await store.is_processing(1) is False


async def test_regression_save_preserves_processing_flag(tmp_path):
    """A handler's ``save()`` during generation must NOT reset the processing flag.

    Every handler ends in ``sessions.save(session)``. While a generation holds
    the per-user processing slot (``processing=1``), a concurrent command or
    message triggers ``save()``; that save must leave the flag untouched or a
    second job could double-process (double extraction / double billing).
    """
    store = _store(tmp_path)
    assert await store.try_acquire_processing(5) is True

    # Any handler calls save() at the end of its work.
    s = await store.get(5)
    s.chat_id = 999
    await store.save(s)

    # The processing flag must survive the save.
    assert await store.is_processing(5) is True
    # A second job must NOT be able to steal the slot.
    assert await store.try_acquire_processing(5) is False
    # Non-flag fields are still persisted normally.
    assert (await store.get(5)).chat_id == 999


async def test_report_title_persists_across_save_load(tmp_path):
    """The user-entered report heading must survive a save/load round-trip.

    Regression: ``report_title`` was in-memory only and dropped on save, so
    the heading set during the heading step was lost when the session was
    reloaded at the password step (falling back to the env default title).
    """
    store = _store(tmp_path)
    s = await store.get(7)
    s.report_title = "My Custom July Expenses"
    s.state = BotState.AWAITING_PASSWORD
    await store.save(s)

    reloaded = await store.get(7)
    assert reloaded.report_title == "My Custom July Expenses"
    assert reloaded.state == BotState.AWAITING_PASSWORD


async def test_sweep_purges_expired_sessions(tmp_path):
    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=31)
    await store.save(Session(user_id=1, chat_id=1, updated_at=past, created_at=past))
    await store.save(Session(user_id=2, chat_id=2))
    res = await store.sweep()
    assert res["purged"] == 1
    assert await store.count() == 1


async def test_sweep_does_not_reclaim_processing(tmp_path):
    """Single-process invariant: the maintenance sweep must never steal a
    processing flag. With the flock singleton making multi-instance impossible,
    there is no lease to reclaim, so a running job's flag must survive a sweep.
    """
    store = _store(tmp_path)
    assert await store.try_acquire_processing(1) is True
    await store.sweep()
    assert await store.is_processing(1) is True


async def test_store_write_does_not_block_event_loop(tmp_path):
    # A store write that must wait on a held SQLite lock must NOT block the
    # event loop (it should run in a worker thread).
    import sqlite3
    import time

    store = _store(tmp_path)
    await store.save(await store.get(1))  # ensure a row
    blocker = sqlite3.connect(str(_db(tmp_path)))
    blocker.execute("BEGIN IMMEDIATE")  # hold the write lock
    done = asyncio.Event()

    async def do_write():
        s = await store.get(1)
        s.chat_id = 2
        await store.save(s)
        done.set()

    asyncio.create_task(do_write())
    await asyncio.sleep(0.05)  # let the write reach the lock
    t0 = time.monotonic()
    await asyncio.sleep(0.05)  # the loop must tick on time
    elapsed = time.monotonic() - t0
    blocker.rollback()  # release the lock
    blocker.close()
    await asyncio.wait_for(done.wait(), timeout=3)
    assert elapsed < 0.15  # far below busy_timeout; loop stayed responsive


async def test_maintenance_loop_runs_sweep(tmp_path):
    from app.main import _maintenance_loop

    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=31)
    await store.save(Session(user_id=1, chat_id=1, updated_at=past, created_at=past))
    task = asyncio.create_task(_maintenance_loop(store.sweep, 0.01))
    await asyncio.sleep(0.06)  # let several iterations run
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The background loop purged the expired session.
    assert await store.count() == 0


async def test_post_init_starts_and_cancels_maintenance_task(tmp_path):
    from app.main import _make_post_init

    store = _store(tmp_path)

    class _FakeBot:
        def __init__(self):
            self.locks = type("L", (), {"evict_idle": lambda idle: 0})()
            self.started = False
            self.stopped = False

        def start_workers(self):
            self.started = True

        async def stop_workers(self):
            self.stopped = True

        async def notify_queued_lost(self):
            return 0

    class _FakeApp:
        def __init__(self):
            self.post_init = None
            self.post_shutdown = None

    bot = _FakeBot()
    app = _FakeApp()
    post_init = _make_post_init(store, 0.01, bot)
    await post_init(app)
    assert app.post_shutdown is not None
    assert bot.started is True
    await asyncio.sleep(0.06)  # let the task run its sweep
    # Shutdown cancels and reaps the task cleanly, and stops the workers.
    await app.post_shutdown(app)
    assert bot.stopped is True


async def test_post_init_notifies_before_starting_workers(tmp_path):
    """Lock the startup ordering: notify_queued_lost (which runs reset_stale)
    must run before start_workers, so reset_stale never clears a live job's
    processing flag."""
    from app.main import _make_post_init

    store = _store(tmp_path)

    class _FakeBot:
        def __init__(self):
            self.locks = type("L", (), {"evict_idle": lambda idle: 0})()
            self.calls = []

        async def notify_queued_lost(self):
            self.calls.append("notify")
            return 0

        def start_workers(self):
            self.calls.append("start")

        async def stop_workers(self):
            pass

    class _FakeApp:
        def __init__(self):
            self.post_init = None
            self.post_shutdown = None

    bot = _FakeBot()
    app = _FakeApp()
    await _make_post_init(store, 0.01, bot)(app)
    assert bot.calls == ["notify", "start"]  # reset_stale runs before workers
    await app.post_shutdown(app)



async def test_purge_expired_clears_stale_processing(tmp_path):
    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=31)
    await store.save(Session(user_id=1, chat_id=1, updated_at=past, created_at=past))
    # Simulate a crashed generation: mark the (expired) row as processing.
    import sqlite3
    conn = sqlite3.connect(str(_db(tmp_path)))
    conn.execute("UPDATE sessions SET processing = 1 WHERE user_id = 1")
    conn.commit()
    conn.close()
    assert await store.purge_expired() == 1
    # After the stale row is purged, a fresh instance can re-acquire the slot.
    assert await store.try_acquire_processing(1) is True


async def test_session_backup_produces_valid_copy(tmp_path):
    import sqlite3

    store = _store(tmp_path)
    s = await store.get(5)
    s.add_file_id("f1")
    await store.save(s)
    dst = store.backup(tmp_path / "backups")
    assert dst.exists()
    conn = sqlite3.connect(str(dst))
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    finally:
        conn.close()


async def test_write_waits_under_contention_not_locked_error(tmp_path):
    # A write against a held SQLite lock must wait (busy_timeout), not fail with
    # "database is locked".
    import asyncio
    import sqlite3
    import threading
    import time

    store = _store(tmp_path)
    await store.save(await store.get(1))  # ensure a row
    blocker = sqlite3.connect(str(_db(tmp_path)))
    blocker.execute("BEGIN IMMEDIATE")
    errors = []

    async def do_write():
        s = await store.get(1)
        s.chat_id = 123
        await store.save(s)

    def worker():
        try:
            asyncio.run(do_write())
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)  # let the write hit the lock
    blocker.rollback()  # release the write lock -> waiting writer proceeds
    blocker.close()
    t.join(timeout=5)
    assert errors == []
    assert (await store.get(1)).chat_id == 123


