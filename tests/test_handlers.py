"""Сценарії бота повністю через aiogram, але без мережі: запити до Telegram перехоплює FakeSession."""
import unittest
from datetime import datetime
from itertools import count
from pathlib import Path

try:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
    from aiogram.types import Chat, Message, Update
except ImportError:  # aiogram не встановлено — ці тести пропускаються
    Bot = None

from bot.config import Config
from bot.db import DB
from bot.services import Store, send_due_reminders

ROOT = Path(__file__).resolve().parent.parent
OLYA, YAROSLAV, STRANGER = 111, 222, 999

if Bot is not None:
    from bot.__main__ import build_dispatcher

    class FakeSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.requests = []
            self._ids = count(1000)

        async def make_request(self, bot, method, timeout=None):
            self.requests.append(method)
            if isinstance(method, SendMessage):
                return Message(message_id=next(self._ids), date=datetime.now(),
                               chat=Chat(id=method.chat_id, type="private"), text=method.text)
            return True

        async def stream_content(self, *args, **kwargs):
            yield b""

        async def close(self):
            pass

    CFG = Config(bot_token="42:TEST", users={OLYA: "Оля", YAROSLAV: "Ярослав"},
                 settings_file=ROOT / "settings.example.json", remind_hour=10)
    SESSION = FakeSession()
    BOT = Bot(CFG.bot_token, session=SESSION)
    DP = build_dispatcher(DB(":memory:"), Store(CFG), CFG)


@unittest.skipIf(Bot is None, "aiogram не встановлено")
class HandlersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DB(":memory:")
        self.store = Store(CFG)
        DP["db"], DP["store"] = self.db, self.store
        self.n = count(1)
        await self.send("/start", OLYA)
        await self.send("/start", YAROSLAV)

    # ---- допоміжне ----
    def _user(self, uid):
        return {"id": uid, "is_bot": False, "first_name": CFG.users.get(uid, "Хтось")}

    async def feed(self, payload):
        SESSION.requests.clear()
        update = Update.model_validate({"update_id": next(self.n), **payload}, context={"bot": BOT})
        await DP.feed_update(BOT, update)
        return SESSION.requests

    async def send(self, text, uid=OLYA):
        return await self.feed({"message": {"message_id": next(self.n), "date": 0, "text": text,
                                            "chat": {"id": uid, "type": "private"}, "from": self._user(uid)}})

    async def press(self, data, uid=OLYA, text="..."):
        msg = {"message_id": 1, "date": 0, "text": text, "chat": {"id": uid, "type": "private"},
               "from": {"id": 42, "is_bot": True, "first_name": "Bot"}}
        return await self.feed({"callback_query": {"id": str(next(self.n)), "from": self._user(uid),
                                                   "chat_instance": "x", "data": data, "message": msg}})

    @staticmethod
    def texts(reqs):
        return [r.text for r in reqs if isinstance(r, (SendMessage, EditMessageText))]

    # ---- сценарії ----
    async def test_stranger_is_blocked(self):
        reqs = await self.send("кава 85", STRANGER)
        self.assertIn("⛔", self.texts(reqs)[0])
        self.assertIn(str(STRANGER), self.texts(reqs)[0])
        self.assertEqual(self.db.last(), [])

    async def test_quick_entry_with_keyword(self):
        reqs = await self.send("кава 85")
        self.assertIn("Записано", self.texts(reqs)[0])
        op = self.db.last()[0]
        self.assertEqual((op.amount, op.category, op.comment, op.user_name), (85, "Кафе", "кава", "Оля"))

    async def test_quick_entry_choose_category_and_switch_to_income(self):
        reqs = await self.send("5000 премія")
        self.assertIn("Оберіть категорію", self.texts(reqs)[0])
        await self.press("kind:income")
        await self.press("cat:0")
        op = self.db.last()[0]
        self.assertEqual((op.kind, op.category, op.amount, op.comment), ("income", "Зарплата", 5000, "премія"))

    async def test_menu_flow(self):
        await self.send("➖ Витрата", YAROSLAV)
        await self.press("cat:2", YAROSLAV)                       # Транспорт
        reqs = await self.send("сто", YAROSLAV)
        self.assertIn("Не схоже на суму", self.texts(reqs)[0])
        await self.send("120+80", YAROSLAV)
        await self.press("skip", YAROSLAV)
        op = self.db.last()[0]
        self.assertEqual((op.category, op.amount, op.comment, op.user_name), ("Транспорт", 200, "", "Ярослав"))

    async def test_menu_button_works_in_the_middle_of_dialog(self):
        await self.send("➖ Витрата")
        await self.press("cat:0")
        reqs = await self.send("📊 Звіт за місяць")
        self.assertIn("📊", self.texts(reqs)[0])
        reqs = await self.send("кава 60")  # діалог скинуто — це знову швидкий запис
        self.assertIn("Записано", self.texts(reqs)[0])

    async def test_edit_category_and_delete(self):
        await self.send("АТБ 1250")
        op = self.db.last()[0]
        await self.press(f"op:amt:{op.id}")
        await self.send("1300")
        await self.press(f"op:cat:{op.id}")
        await self.press(f"setcat:{op.id}:7")                      # Дім
        op = self.db.get(op.id)
        self.assertEqual((op.amount, op.category), (1300, "Дім"))
        await self.press(f"op:del:{op.id}")
        self.assertFalse(self.db.get(op.id).deleted)               # спочатку лише підтвердження
        reqs = await self.press(f"op:delok:{op.id}", YAROSLAV)
        self.assertTrue(self.db.get(op.id).deleted)
        self.assertIn("Видалено", self.texts(reqs)[0])

    async def test_limit_warning(self):
        await self.send("2300 кафе")
        reqs = await self.send("кава 150")                          # 2450 з 3000 — перетнули 80%
        self.assertIn("⚠️", self.texts(reqs)[0])

    async def test_recurring_payment_paid_once(self):
        rec = self.store.settings.recurring[1]                      # Інтернет 250
        await self.press(f"pay:{rec.key}", OLYA)
        reqs = await self.press(f"pay:{rec.key}", YAROSLAV)          # другий натискає пізніше
        alert = [r for r in reqs if isinstance(r, AnswerCallbackQuery)][0]
        self.assertIn("Вже оплачено (Оля)", alert.text)
        ops = self.db.last()
        self.assertEqual(len(ops), 1)
        self.assertEqual((ops[0].amount, ops[0].category), (250, "Зв'язок"))

    async def test_recurring_other_amount(self):
        rec = self.store.settings.recurring[3]                      # Комуналка
        await self.press(f"payx:{rec.key}")
        await self.send("2875,40")
        self.assertEqual(self.db.last()[0].amount, 2875.4)
        self.assertTrue(self.db.is_paid(rec.key, CFG.now().strftime("%Y-%m")))

    async def test_reminders_sent_once_per_day(self):
        now = datetime(2026, 10, 4, 10, 5)                          # Інтернет 5-го, Садочок 1-го
        SESSION.requests.clear()
        sent = await send_due_reminders(BOT, self.db, self.store, now)
        self.assertEqual(sent, 4)                                   # 2 платежі × 2 людини
        self.assertEqual(await send_due_reminders(BOT, self.db, self.store, now), 0)
        self.assertEqual(await send_due_reminders(BOT, self.db, self.store, datetime(2026, 10, 5, 9)), 0)  # рано


if __name__ == "__main__":
    unittest.main()
