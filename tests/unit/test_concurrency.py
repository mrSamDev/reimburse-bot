"""Tests for per-user concurrency locks."""

import asyncio

import pytest

from app.bot.locks import UserLockManager


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
