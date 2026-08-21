"""Tests for the Session model and SQLite-backed SessionStore."""

from datetime import datetime, timedelta, timezone

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
    assert not s.processing


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


def _force_lease_expiry(tmp_path, user_id: int) -> None:
    import sqlite3

    conn = sqlite3.connect(str(_db(tmp_path)))
    conn.execute(
        "UPDATE sessions SET lease_expiry = ? WHERE user_id = ?",
        ("2000-01-01T00:00:00+00:00", user_id),
    )
    conn.commit()
    conn.close()


async def test_stale_lease_reclaimable_by_other_instance(tmp_path):
    a = _store(tmp_path)
    b = _store(tmp_path)
    assert await a.try_acquire_processing(1) is True
    # Simulate a crash: the lease is left set but long expired.
    _force_lease_expiry(tmp_path, 1)
    # Another instance must be able to reclaim the abandoned lease.
    assert await b.try_acquire_processing(1) is True
    assert await a.try_acquire_processing(1) is False  # now held by b


async def test_live_lease_not_reacquirable(tmp_path):
    a = _store(tmp_path)
    b = _store(tmp_path)
    assert await a.try_acquire_processing(1) is True
    # A still-live lease (not expired) blocks other instances.
    assert await b.try_acquire_processing(1) is False


async def test_release_clears_processing_and_lease(tmp_path):
    store = _store(tmp_path)
    assert await store.try_acquire_processing(1) is True
    await store.release_processing(1)
    # After release the slot is re-acquirable.
    assert await store.try_acquire_processing(1) is True



async def test_purge_expired_clears_stale_processing(tmp_path):
    store = _store(tmp_path, ttl_seconds=30)
    past = datetime.now(timezone.utc) - timedelta(seconds=31)
    await store.save(Session(user_id=1, chat_id=1, processing=True, updated_at=past, created_at=past))
    removed = await store.purge_expired()
    assert removed == 1
    # After a crash, a fresh instance can re-acquire the slot.
    assert await store.try_acquire_processing(1) is True


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


