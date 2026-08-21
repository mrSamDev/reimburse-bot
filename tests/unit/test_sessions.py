"""Tests for Session model + SessionStore."""

from datetime import datetime, timedelta, timezone

from app.bot.states import BotState
from app.models.session import Session
from app.services.session_service import SessionStore


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


def test_store_isolation_between_users():
    store = SessionStore()
    s1 = store.get(10)
    s1.add_file_id("fA")
    s2 = store.get(20)
    assert s2.receipt_file_ids == []


def test_store_returns_same_session():
    store = SessionStore()
    assert store.get(5) is store.get(5)


def test_store_clear():
    store = SessionStore()
    store.get(5)
    store.clear(5)
    assert store.get(5).receipt_file_ids == []


def test_not_expired_within_ttl():
    store = SessionStore(ttl_seconds=60)
    s = store.get(5)
    s.add_file_id("f1")
    s.touch()
    later = datetime.now(timezone.utc) + timedelta(seconds=10)
    assert not s.is_expired(60, now=later)
    # Not expired -> get() returns the same live session.
    assert store.get(5) is s
    assert store.get(5).receipt_file_ids == ["f1"]


def test_expired_session_returns_fresh_emptied_session():
    store = SessionStore(ttl_seconds=30)
    store.get(5).add_file_id("f1")
    now = datetime.now(timezone.utc)
    store.get_locked(5).updated_at = now - timedelta(seconds=31)
    fresh = store.get(5)
    assert fresh.receipt_file_ids == []
    assert fresh.user_id == 5
    # The stale one must be gone from the registry.
    assert store.get(5).updated_at > now - timedelta(seconds=31)


def test_touch_updates_updated_at():
    s = Session(user_id=1, chat_id=1)
    before = s.updated_at
    s.add_file_id("f1")
    assert s.updated_at >= before


def test_multiple_users_independent_receipts():
    store = SessionStore()
    store.get(1).add_file_id("u1a")
    store.get(2).add_file_id("u2a")
    store.get(2).add_file_id("u2b")
    assert store.get(1).receipt_file_ids == ["u1a"]
    assert store.get(2).receipt_file_ids == ["u2a", "u2b"]
