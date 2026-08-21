"""Authorization and password-gate security service."""

from __future__ import annotations

import hmac

from app.config import Config


class AuthError(Exception):
    """Raised for authorization failures."""


class SecurityService:
    """Stateless helpers for user authorization and password verification."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._allowed_users = set(config.allowed_user_ids)
        self._allowed_chats = set(config.allowed_chat_ids)

    def is_authorized_user(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if not self._allowed_users:
            # No allow-list configured: deny by default (private bot).
            return False
        return user_id in self._allowed_users

    def is_authorized_chat(self, chat_id: int | None) -> bool:
        if not self._allowed_chats:
            return True  # chat restrictions optional
        if chat_id is None:
            return False
        return chat_id in self._allowed_chats

    def is_authorized(self, user_id: int | None, chat_id: int | None) -> bool:
        return self.is_authorized_user(user_id) and self.is_authorized_chat(chat_id)

    def check_authorized(self, user_id: int | None, chat_id: int | None) -> None:
        if not self.is_authorized(user_id, chat_id):
            raise AuthError("Unauthorized")

    @property
    def has_password(self) -> bool:
        return bool(self._config.bot_password)

    def check_password(self, candidate: str) -> bool:
        """Constant-time comparison; never logs the password."""
        expected = self._config.bot_password
        if not expected:
            return False
        return hmac.compare_digest(expected.encode("utf-8"), candidate.encode("utf-8"))
