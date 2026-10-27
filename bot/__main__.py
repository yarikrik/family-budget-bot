"""Запуск: python -m bot"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import Config, load_config
from .db import DB
from .handlers import router
from .middleware import AccessMiddleware
from .services import Store, reminder_loop, sync_loop
from .sheets import SheetsSync

log = logging.getLogger("bot")


def build_dispatcher(db: DB, store: Store, cfg: Config) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"], dp["store"], dp["cfg"] = db, store, cfg
    access = AccessMiddleware(set(cfg.users))
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(router)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    db = DB(cfg.db_path)

    store = Store(cfg)
    if cfg.sheet_id:
        try:
            store.sheets = await asyncio.to_thread(SheetsSync.connect, cfg.google_credentials, cfg.sheet_id)
            await asyncio.to_thread(store.sheets.ensure_structure, store.settings)
            await store.reload()
            log.info("Google Sheets підключено")
        except Exception:
            log.exception("Google Sheets недоступний — працюю лише з локальною базою")
            store.sheets = None
    log.info("Категорій: %d, регулярних платежів: %d", len(store.settings.categories), len(store.settings.recurring))

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = build_dispatcher(db, store, cfg)

    await bot.set_my_commands([
        BotCommand(command="report", description="Звіт за місяць"),
        BotCommand(command="last", description="Останні записи"),
        BotCommand(command="payments", description="Регулярні платежі"),
        BotCommand(command="reload", description="Оновити категорії з таблиці"),
        BotCommand(command="cancel", description="Скасувати"),
        BotCommand(command="help", description="Як користуватись"),
    ])
    tasks = [asyncio.create_task(sync_loop(db, store)), asyncio.create_task(reminder_loop(bot, db, store))]
    log.info("Бот запущено")
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
