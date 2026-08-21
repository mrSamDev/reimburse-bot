"""Cross-process singleton lock (flock-based).

Prevents a second bot instance from polling the same Telegram token, which
would otherwise fail ``getUpdates`` with a 409 "Conflict: terminated by other
getUpdates request". Uses ``fcntl.flock`` so the kernel releases the lock
automatically when the owning process dies, even on SIGKILL/OOM — no stale
locks, no heartbeat needed.

The lock file lives in the shared ``data_dir`` volume, so any duplicate
container sharing that volume competes for the same lock.
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path


class InstanceLockError(Exception):
    """Raised when the instance lock is already held by another process."""


class InstanceLock:
    """A non-blocking exclusive lock on a file.

    Typical use: hold one ``InstanceLock`` for the lifetime of the bot process
    and release it on shutdown. A second process trying to ``acquire()`` the
    same path gets ``False`` and should refuse to start.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to take the exclusive lock.

        Returns ``True`` on success, ``False`` if another instance already
        holds it. Re-acquiring on the same object is a no-op success.
        """
        if self._fd is not None:
            return True
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                # Locked by another process — not a real error.
                return False
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        """Unlock and close the lock file. Safe to call when not held."""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise InstanceLockError(
                f"instance lock already held: {self.path}"
            )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
