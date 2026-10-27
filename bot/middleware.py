"""Доступ лише для членів сім'ї зі списку ALLOWED_USERS."""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    def __init__(self, allowed: set[int]):
        self.allowed = allowed

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None and user.id in self.allowed:
            return await handler(event, data)
        uid = user.id if user else "?"
        log.info("Відхилено доступ для %s", uid)
        if isinstance(event, Message):
            await event.answer("⛔ Це приватний сімейний бот.\n"
                               f"Ваш Telegram ID: <code>{uid}</code> — передайте його власнику, "
                               "щоб він додав вас до списку.")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Немає доступу", show_alert=True)
        return None
