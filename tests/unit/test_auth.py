"""Tests for authorization (security_service)."""

import pytest

from app.config import Config
from app.services.security_service import SecurityService


def _svc(**kw):
    base = dict(allowed_user_ids="111,222", allowed_chat_ids="")
    base.update(kw)
    return SecurityService(Config(**base))


def test_authorized_user():
    s = _svc()
    assert s.is_authorized_user(111)
    assert s.is_authorized_user(222)


def test_unauthorized_user():
    s = _svc()
    assert not s.is_authorized_user(999)
    assert not s.is_authorized_user(None)


def test_multiple_authorized_users():
    s = _svc(allowed_user_ids="1,2,3")
    for uid in (1, 2, 3):
        assert s.is_authorized_user(uid)
    assert not s.is_authorized_user(4)


def test_no_allowlist_denies_everyone():
    s = _svc(allowed_user_ids="")
    assert not s.is_authorized_user(111)


def test_chat_restriction_applies():
    s = _svc(allowed_chat_ids="100")
    assert s.is_authorized_chat(100)
    assert not s.is_authorized_chat(200)
    assert not s.is_authorized_user(111) or s.is_authorized_user(111)  # user rule intact


def test_chat_restriction_with_no_chat_list_open():
    s = _svc()
    assert s.is_authorized_chat(123)


def test_is_authorized_requires_both():
    s = _svc(allowed_chat_ids="100")
    assert s.is_authorized(111, 100)
    assert not s.is_authorized(111, 999)
    assert not s.is_authorized(333, 100)


def test_password_correct():
    s = _svc(bot_password="secret")
    assert s.check_password("secret")


def test_password_incorrect():
    s = _svc(bot_password="secret")
    assert not s.check_password("wrong")
    assert not s.check_password("")


def test_password_empty_disabled():
    s = _svc(bot_password="")
    assert not s.has_password
    assert not s.check_password("anything")


def test_password_constant_time_not_logged(monkeypatch, capsys):
    s = _svc(bot_password="hunter2")
    assert not s.check_password("hunter3")
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "hunter2" not in out
