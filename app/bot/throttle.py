"""Per-user password attempt throttle (in-memory lockout).

Brute-forcing the shared password is cheap (one shared secret, constant-time
compare). This throttle locks a user out after ``max_attempts`` consecutive
failures for ``lockout_seconds``; a correct password resets the counter.

In-memory and per-process by design: a lockout does not need to survive a
restart, and it keeps the store (and cross-process semantics) untouched.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class PasswordThrottle:
    """Tracks consecutive failed password attempts per user with a lockout window.

    ``_now`` is injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        lockout_seconds: int = 300,
        _now: Callable[[], float] | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._now = _now or time.monotonic
        self._attempts: dict[int, int] = {}
        self._locked_until: dict[int, float] = {}

    def is_locked(self, user_id: int) -> bool:
        until = self._locked_until.get(user_id)
        return until is not None and self._now() < until

    def remaining_attempts(self, user_id: int) -> int:
        return max(0, self._max_attempts - self._attempts.get(user_id, 0))

    def record_failure(self, user_id: int) -> None:
        n = self._attempts.get(user_id, 0) + 1
        self._attempts[user_id] = n
        if n >= self._max_attempts:
            self._locked_until[user_id] = self._now() + self._lockout_seconds

    def reset(self, user_id: int) -> None:
        self._attempts.pop(user_id, None)
        self._locked_until.pop(user_id, None)
