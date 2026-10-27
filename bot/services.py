"""Спільний стан бота: налаштування, синхронізація з таблицею, фонові задачі."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from .config import Config
from .db import DB, Operation
from .reminders import due_reminders, format_reminder
from .reports import history, shift_month, summarize
from .settings import Settings, load_settings_file
from .sheets import SheetsSync, dashboard_matrix

log = logging.getLogger(__name__)
SETTINGS_RELOAD_SEC = 600


class Store:
    """Тримає актуальні налаштування. Джерело — Google Sheets (якщо підключено) або settings.json."""

    def __init__(self, cfg: Config, sheets: SheetsSync | None = None):
        self.cfg = cfg
        self.sheets = sheets
        self.settings: Settings = load_settings_file(cfg.settings_file)
        self._dirty = True

    async def reload(self) -> Settings:
        if self.sheets:
            try:
                new = await asyncio.to_thread(self.sheets.load_settings)
                if new.categories:
                    self.settings = new
                else:
                    log.warning("Аркуш «Категорії» порожній — лишаю попередні налаштування")
            except Exception:
                log.exception("Не вдалося прочитати налаштування з Google Sheets")
        else:
            self.settings = load_settings_file(self.cfg.settings_file)
        return self.settings

    def touch(self) -> None:
        """Позначити, що дані змінились і треба синхронізувати таблицю."""
        self._dirty = True

    def month_spent(self, db: DB, op: Operation) -> float:
        return round(sum(o.amount for o in db.period(op.created_at.year, op.created_at.month)
                         if o.kind == op.kind and o.category == op.category), 2)

    async def sync(self, db: DB) -> None:
        if not self.sheets:
            return
        pending = db.pending()
        if pending:
            done = await asyncio.to_thread(self.sheets.push, pending)
            db.mark_synced(done)
            log.info("Синхронізовано з таблицею: %d", len(done))
            self._dirty = True
        if self._dirty:
            now = self.cfg.now()
            start_y, start_m = shift_month(now.year, now.month, -11)
            ops = db.since(datetime(start_y, start_m, 1))
            matrix = dashboard_matrix(summarize(ops, now.year, now.month), history(ops), self.settings, now)
            await asyncio.to_thread(self.sheets.write_dashboard, matrix)
            self._dirty = False


async def sync_loop(db: DB, store: Store) -> None:
    last_reload = time.monotonic()
    while True:
        try:
            await store.sync(db)
            if store.sheets and time.monotonic() - last_reload > SETTINGS_RELOAD_SEC:
                await store.reload()
                last_reload = time.monotonic()
        except Exception:
            log.exception("Помилка синхронізації — повторю пізніше")
        await asyncio.sleep(store.cfg.sync_interval)


async def send_due_reminders(bot, db: DB, store: Store, now: datetime | None = None) -> int:
    from .keyboards import pay_kb  # aiogram потрібен лише тут

    cfg = store.cfg
    now = now or cfg.now()
    if now.hour < cfg.remind_hour:
        return 0
    today = now.date()
    sent = 0
    for rec, due in due_reminders(store.settings, today, db.is_paid, cfg.remind_days_before):
        if db.reminder_sent(rec.key, today.isoformat()):
            continue
        text = format_reminder(rec, due, today, cfg.currency)
        for uid in cfg.users:
            try:
                await bot.send_message(uid, text, reply_markup=pay_kb(rec.key))
                sent += 1
            except Exception as e:  # користувач ще не запускав бота або заблокував його
                log.warning("Не вдалося надіслати нагадування %s: %s", uid, e)
        db.mark_reminder(rec.key, today.isoformat())
    return sent


async def reminder_loop(bot, db: DB, store: Store) -> None:
    while True:
        try:
            await send_due_reminders(bot, db, store)
        except Exception:
            log.exception("Помилка нагадувань")
        await asyncio.sleep(300)
