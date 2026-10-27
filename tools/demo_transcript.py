"""Проганяє демо-сценарії через справжні хендлери (без мережі) і зберігає діалоги в JSON.
З цього JSON зроблені скріншоти в README. Запуск: python tools/demo_transcript.py"""
import asyncio
import json
import random
import sys
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage  # noqa: E402
from aiogram.types import Chat, Message, Update  # noqa: E402

from bot.__main__ import build_dispatcher  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.db import DB  # noqa: E402
from bot.services import Store, send_due_reminders  # noqa: E402
from bot.settings import EXPENSE, INCOME  # noqa: E402

OLYA, YAROSLAV = 111, 222
LOG: list[dict] = []


def markup(m):
    if m is None:
        return None
    d = m.model_dump(exclude_none=True)
    if "inline_keyboard" in d:
        return {"inline": [[b["text"] for b in row] for row in d["inline_keyboard"]]}
    return None


class Session(BaseSession):
    ids = count(500)

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            LOG.append({"from": "bot", "text": method.text, "kb": markup(method.reply_markup)})
            return Message(message_id=next(self.ids), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        if isinstance(method, EditMessageText):
            LOG.append({"from": "bot", "edit": True, "text": method.text, "kb": markup(method.reply_markup)})
        if isinstance(method, AnswerCallbackQuery) and method.text:
            LOG.append({"from": "toast", "text": method.text})
        return True

    async def stream_content(self, *a, **k):
        yield b""

    async def close(self):
        pass


async def main():
    cfg = Config(bot_token="42:DEMO", users={OLYA: "Оля", YAROSLAV: "Ярослав"},
                 settings_file=ROOT / "settings.example.json")
    db, store = DB(":memory:"), Store(cfg)
    bot = Bot(cfg.bot_token, session=Session())
    dp = build_dispatcher(db, store, cfg)
    n = count(1)
    now = cfg.now()

    async def feed(payload):
        await dp.feed_update(bot, Update.model_validate({"update_id": next(n), **payload}, context={"bot": bot}))

    def user(uid):
        return {"id": uid, "is_bot": False, "first_name": cfg.users[uid]}

    async def say(text, uid=OLYA):
        LOG.append({"from": "user", "text": text})
        await feed({"message": {"message_id": next(n), "date": 0, "text": text,
                                "chat": {"id": uid, "type": "private"}, "from": user(uid)}})

    async def press(data, label, uid=OLYA):
        LOG.append({"from": "press", "text": label})
        msg = {"message_id": 1, "date": 0, "text": "...", "chat": {"id": uid, "type": "private"},
               "from": {"id": 42, "is_bot": True, "first_name": "Bot"}}
        await feed({"callback_query": {"id": str(next(n)), "from": user(uid), "chat_instance": "x",
                                       "data": data, "message": msg}})

    # фонові дані за місяць, щоб звіт був живим
    random.seed(7)
    plan = [(INCOME, "Зарплата", 32000, YAROSLAV, 5), (INCOME, "Зарплата", 24500, OLYA, 7),
            (INCOME, "Підробіток", 6800, YAROSLAV, 18)]
    for cat, lo, hi, k in [("Продукти", 180, 1400, 16), ("Кафе", 60, 420, 9), ("Транспорт", 70, 260, 10),
                           ("Здоров'я", 120, 700, 2), ("Діти", 200, 900, 3), ("Розваги", 99, 450, 3),
                           ("Дім", 300, 1600, 2)]:
        for _ in range(k):
            plan.append((EXPENSE, cat, random.randrange(lo, hi, 5), random.choice([OLYA, YAROSLAV]),
                         random.randint(1, now.day - 1)))
    for kind, cat, amount, uid, day in plan:
        db.add(kind, amount, cat, "", uid, cfg.users[uid], now.replace(day=day, hour=12))
    for rec in store.settings.recurring[:4]:  # усе, крім Netflix, уже оплачено
        op = db.add(EXPENSE, rec.amount, rec.category, f"{rec.name} (регулярний платіж)", OLYA, "Оля",
                    now.replace(day=rec.day, hour=19))
        db.mark_paid(rec.key, now.strftime("%Y-%m"), op.id)

    chats = {}

    LOG.clear()
    await say("/start")
    chats["start"] = LOG[:]

    LOG.clear()
    await say("кава 85")
    await say("АТБ 1 250,40")
    chats["quick"] = LOG[:]

    LOG.clear()
    await say("5000 премія", YAROSLAV)
    await press("kind:income", "🔄 Це дохід", YAROSLAV)
    await press("cat:1", "💻 Підробіток", YAROSLAV)
    chats["income"] = LOG[:]

    LOG.clear()
    atb = db.last()[1]
    await say("🧾 Останні записи")
    await press(f"op:show:{atb.id}", "2")
    await press(f"op:amt:{atb.id}", "✏️ Сума")
    await say("1 190")
    chats["edit"] = LOG[:]

    LOG.clear()
    await say("📊 Звіт за місяць")
    chats["report"] = LOG[:]

    LOG.clear()
    await send_due_reminders(bot, db, store, now.replace(day=27, hour=10))  # Netflix 28-го
    LOG[:] = LOG[:1]  # однакове повідомлення обом — показуємо одне
    await press(f"pay:{store.settings.recurring[4].key}", "✅ Оплачено")
    await say("🔁 Регулярні платежі")
    chats["recurring"] = LOG[:]

    out = ROOT / "tools" / "demo_transcript.json"
    out.write_text(json.dumps({"now": now.isoformat(), "chats": chats}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out, {k: len(v) for k, v in chats.items()})


if __name__ == "__main__":
    asyncio.run(main())
