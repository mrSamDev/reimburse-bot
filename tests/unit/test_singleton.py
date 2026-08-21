"""Tests for the cross-process singleton lock (app/utils/singleton.py).

The lock prevents a second bot instance from polling the same Telegram token,
which would otherwise 409-conflict with the first instance's ``getUpdates``
loop. flock is used so the kernel releases the lock automatically when the
owning process dies, even on SIGKILL/OOM.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.utils.singleton import InstanceLock, InstanceLockError


def test_acquire_creates_lock_file(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    lock = InstanceLock(lock_path)
    assert lock.acquire() is True
    assert lock_path.exists()
    lock.release()


def test_second_acquire_fails_while_held(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    first = InstanceLock(lock_path)
    second = InstanceLock(lock_path)
    assert first.acquire() is True
    # flock treats two separate open() fds of the same file independently,
    # so an exclusive non-blocking lock on the second fd must conflict.
    assert second.acquire() is False
    first.release()


def test_acquire_succeeds_after_release(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    first = InstanceLock(lock_path)
    second = InstanceLock(lock_path)
    assert first.acquire() is True
    first.release()
    assert second.acquire() is True
    second.release()


def test_acquire_is_idempotent_within_instance(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    lock = InstanceLock(lock_path)
    assert lock.acquire() is True
    # Re-acquiring the same lock object must not error or double-lock.
    assert lock.acquire() is True
    lock.release()
    # Released, so a fresh instance can take it.
    assert InstanceLock(lock_path).acquire() is True


def test_context_manager_raises_when_held(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    first = InstanceLock(lock_path)
    assert first.acquire() is True
    with pytest.raises(InstanceLockError):
        with InstanceLock(lock_path):
            pass
    first.release()


def test_context_manager_releases_on_exit(tmp_path: Path):
    lock_path = tmp_path / "instance.lock"
    with InstanceLock(lock_path):
        assert InstanceLock(lock_path).acquire() is False
    # Released after the `with` block.
    assert InstanceLock(lock_path).acquire() is True


def test_lock_auto_releases_on_process_death(tmp_path: Path):
    """The guarantee that justifies flock over a DB-row lock: the kernel
    releases the lock when the holding process dies (kill, not clean exit)."""
    lock_path = tmp_path / "instance.lock"
    marker = tmp_path / "marker"
    child = (
        "import sys\n"
        "from pathlib import Path\n"
        "from app.utils.singleton import InstanceLock\n"
        "lock = InstanceLock(sys.argv[1])\n"
        "ok = lock.acquire()\n"
        "Path(sys.argv[2]).write_text('acquired' if ok else 'held')\n"
        "if ok:\n"
        "    sys.stdin.read()\n"  # hold the lock until killed
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", child, str(lock_path), str(marker)],
        stdin=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 30
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "child never reported"
        assert marker.read_text() == "acquired"
    finally:
        proc.kill()
        proc.wait()

    # Child is dead -> its fd is closed by the kernel -> lock is free.
    assert InstanceLock(lock_path).acquire() is True
