"""PTB command handlers, exposed as methods on :class:`ReimbursementBot`."""

from __future__ import annotations

from app.bot import messages as msg
from app.bot.base import _BotBase
from app.bot.logic import (
    handle_cancel,
    handle_clear,
    handle_generate,
    handle_help,
    handle_start,
    handle_status,
)
from app.bot.states import BotState


class CommandHandlersMixin(_BotBase):
    """start/help/status/clear/cancel/generate command handlers."""

    async def start_command(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = auth
        await self.sessions.set_chat_id(user.id, update.effective_chat.id)
        state, reply = handle_start()
        await self.sessions.set_state(user.id, state)
        await self._reply(update, reply)

    async def help_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        await self._reply(update, handle_help()[1])

    async def status_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
        await self._reply(update, handle_status(session)[1])

    async def clear_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
        if session.state in (BotState.PROCESSING, BotState.QUEUED):
            await self._reply(update, msg.BUSY)
            return
        state, reply = handle_clear(session)  # clears receipts on the detached copy
        session.state = state
        session.report_title = ""
        await self.sessions.clear_receipts(user.id)  # atomic SQL clear
        await self.sessions.save(session)
        await self._reply(update, reply)

    async def cancel_command(self, update, context) -> None:
        if self._authorized(update) is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, _ = self._authorized(update)
        session = await self.sessions.get(user.id)
        if session.state in (BotState.PROCESSING, BotState.QUEUED):
            await self._reply(update, msg.BUSY)
            return
        state, reply = handle_cancel(session)
        session.state = state
        await self.sessions.save(session)
        await self._reply(update, reply)

    async def generate_command(self, update, context) -> None:
        auth = self._authorized(update)
        if auth is None:
            await self._reply(update, msg.UNAUTHORIZED)
            return
        user, chat = auth
        session = await self.sessions.get(user.id)
        session.chat_id = chat.id
        processing = (
            await self.sessions.is_processing(user.id)
            or bool(self.locks.get(user.id).locked())
            or session.state == BotState.QUEUED
        )
        state, reply = handle_generate(
            session, has_password=self.security.has_password, processing=processing
        )
        if state is not None:
            session.state = state
        await self.sessions.save(session)
        if reply:
            await self._reply(update, reply)
