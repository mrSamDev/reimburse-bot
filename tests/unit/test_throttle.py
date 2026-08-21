"""Tests for the per-user password attempt throttle."""

from app.bot.throttle import PasswordThrottle


def _clock():
    """Return a controllable monotonic clock (reader + setter)."""
    state = {"t": 0.0}
    return lambda: state["t"], state


def test_not_locked_until_max_attempts():
    now, state = _clock()
    t = PasswordThrottle(max_attempts=3, lockout_seconds=300, _now=now)
    assert t.remaining_attempts(1) == 3
    t.record_failure(1)
    t.record_failure(1)
    assert t.is_locked(1) is False
    assert t.remaining_attempts(1) == 1
    t.record_failure(1)  # reaches max -> lockout
    assert t.is_locked(1) is True
    assert t.remaining_attempts(1) == 0


def test_locks_after_window_until_time_passes():
    now, state = _clock()
    t = PasswordThrottle(max_attempts=1, lockout_seconds=300, _now=now)
    t.record_failure(1)
    assert t.is_locked(1) is True
    state["t"] = 299
    assert t.is_locked(1) is True
    state["t"] = 300.5
    assert t.is_locked(1) is False


def test_reset_clears_failures_and_lockout():
    now, state = _clock()
    t = PasswordThrottle(max_attempts=1, lockout_seconds=300, _now=now)
    t.record_failure(1)
    assert t.is_locked(1) is True
    t.reset(1)
    assert t.is_locked(1) is False
    assert t.remaining_attempts(1) == 1


def test_users_are_independent():
    now, _ = _clock()
    t = PasswordThrottle(max_attempts=2, lockout_seconds=300, _now=now)
    t.record_failure(1)
    t.record_failure(1)
    assert t.is_locked(1) is True
    assert t.is_locked(2) is False
    assert t.remaining_attempts(2) == 2
