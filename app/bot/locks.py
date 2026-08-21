"""Per-user asyncio lock management for concurrency control."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class UserLockManager:
    """One :class:`asyncio.Lock` per user so concurrent /generate calls are
    serialized without blocking different users.

    Locks are created lazily and removed once they have been idle for
    ``evict_idle`` seconds and are not currently held, so a long-running bot
    does not accumulate one lock object per user ever seen.
    """

    def __init__(self, *, _now: Callable[[], float] | None = None) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_used: dict[int, float] = {}
        self._now = _now or time.monotonic

    def get(self, user_id: int) -> asyncio.Lock:
        """Return (creating if needed) the lock for ``user_id``, marking it used."""
        self._last_used[user_id] = self._now()
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def acquire(self, user_id: int) -> bool:
        """Acquire the lock without waiting; returns False if already held."""
        lock = self.get(user_id)
        if lock.locked():
            return False
        await lock.acquire()
        self._last_used[user_id] = self._now()
        return True

    def release(self, user_id: int) -> None:
        lock = self._locks.get(user_id)
        if lock is None:
            return
        if lock.locked():
            lock.release()
        self._last_used[user_id] = self._now()

    def evict_idle(self, idle_seconds: float, *, now: float | None = None) -> int:
        """Drop locks unused for ``idle_seconds`` (excluding any currently held).

        Returns how many locks were evicted. ``now`` is injectable for tests.
        """
        now = self._now() if now is None else now
        evicted = 0
        for user_id, lock in list(self._locks.items()):
            if lock.locked():
                continue  # in-flight generation: never evict
            last = self._last_used.get(user_id, 0.0)
            if now - last > idle_seconds:
                del self._locks[user_id]
                self._last_used.pop(user_id, None)
                evicted += 1
        return evicted
