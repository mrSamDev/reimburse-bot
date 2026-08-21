"""Per-user asyncio lock management for concurrency control."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class UserLockManager:
    """One :class:`asyncio.Lock` per user so concurrent /generate calls are
    serialized without blocking different users."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]

    async def acquire(self, user_id: int) -> bool:
        """Acquire the lock without waiting; returns False if already held."""
        lock = self.get(user_id)
        if lock.locked():
            return False
        await lock.acquire()
        return True

    def release(self, user_id: int) -> None:
        lock = self.get(user_id)
        if lock.locked():
            lock.release()
