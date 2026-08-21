"""Tests for per-user concurrency locks."""

import asyncio

from app.bot.locks import UserLockManager


def _clock():
    """Return a controllable monotonic clock (``now()`` reader + ``set``)."""
    state = {"t": 0.0}
    return lambda: state["t"], state


def test_evict_idle_removes_unused_locks():
    now, state = _clock()
    m = UserLockManager(_now=now)
    m.get(1)  # touched at t=0
    state["t"] = 5
    assert m.evict_idle(10) == 0  # recent -> kept
    assert 1 in m._locks
    state["t"] = 15
    assert m.evict_idle(10) == 1  # 15-0 > 10 -> idle, evicted
    assert 1 not in m._locks


def test_held_lock_never_evicted():
    now, state = _clock()
    m = UserLockManager(_now=now)

    async def acquire():
        assert await m.acquire(1) is True

    asyncio.run(acquire())
    state["t"] = 999
    assert m.evict_idle(10) == 0  # still held -> never evicted
    assert 1 in m._locks


def test_recently_used_lock_not_evicted():
    now, state = _clock()
    m = UserLockManager(_now=now)
    m.get(1)  # touched at t=0
    state["t"] = 5
    assert m.evict_idle(10) == 0
    assert 1 in m._locks


def test_evict_cleans_both_maps():
    now, state = _clock()
    m = UserLockManager(_now=now)
    m.get(1)
    m.get(2)
    state["t"] = 100
    assert m.evict_idle(10) == 2
    assert not m._locks
    assert not m._last_used


async def test_lock_acquire_release():
    m = UserLockManager()
    assert await m.acquire(1) is True
    assert m.get(1).locked()
    m.release(1)
    assert not m.get(1).locked()


async def test_second_acquire_same_user_fails():
    m = UserLockManager()
    await m.acquire(1)
    assert await m.acquire(1) is False
    m.release(1)


async def test_different_users_independent():
    m = UserLockManager()
    assert await m.acquire(1) is True
    # Different user unaffected.
    assert await m.acquire(2) is True
    assert await m.acquire(1) is False
    m.release(1)
    m.release(2)


async def test_concurrent_generate_only_one_runs():
    m = UserLockManager()
    running = []
    done = []

    async def job():
        if not await m.acquire(10):
            return "blocked"
        running.append("started")
        await asyncio.sleep(0.02)
        running.append("finished")
        m.release(10)
        done.append("ok")
        return "ran"

    results = await asyncio.gather(job(), job(), job())
    assert results.count("ran") == 1
    assert results.count("blocked") == 2
    assert done == ["ok"]


async def test_release_without_acquire_is_safe():
    m = UserLockManager()
    m.release(99)  # no-op, no exception
