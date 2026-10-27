"""Тести логіки бота (без Telegram і без Google): python -m pytest"""
import unittest
from datetime import date, datetime
from pathlib import Path

from bot.db import DB
from bot.money import fmt_money, parse_amount, parse_quick
from bot.reminders import due_date, due_reminders, format_reminder, when_text
from bot.reports import format_month, history, limit_warning, shift_month, summarize
from bot.settings import (EXPENSE, INCOME, Recurring, load_settings_file, settings_from_rows,
                          settings_to_rows)
from bot.sheets import DASHBOARD, OPS, SheetsSync, dashboard_matrix

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = load_settings_file(ROOT / "settings.example.json")
T = datetime(2026, 10, 27, 18, 40)


class MoneyTest(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(parse_amount("250"), 250)
        self.assertEqual(parse_amount("1 250,50"), 1250.5)
        self.assertEqual(parse_amount("120+85+40"), 245)
        self.assertEqual(parse_amount("99 грн"), 99)
        for bad in ("", "abc", "0", "-5", "1.2.3", "12+", None):
            self.assertIsNone(parse_amount(bad), bad)

    def test_parse_quick(self):
        self.assertEqual(parse_quick("кава 85"), (85, "кава"))
        self.assertEqual(parse_quick("85 кава"), (85, "кава"))
        self.assertEqual(parse_quick("АТБ 1 250,40"), (1250.4, "АТБ"))
        self.assertEqual(parse_quick("таксі 120+95"), (215, "таксі"))
        self.assertEqual(parse_quick("300"), (300, ""))
        self.assertIsNone(parse_quick("привіт"))

    def test_fmt(self):
        self.assertEqual(fmt_money(1250), "1 250 грн")
        self.assertEqual(fmt_money(99.5), "99,50 грн")
        self.assertEqual(fmt_money(-3400, "₴"), "-3 400 ₴")


class SettingsTest(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(SETTINGS.find_by_keyword("Кава з колегами").name, "Кафе")
        self.assertEqual(SETTINGS.find_by_keyword("АТБ").name, "Продукти")
        self.assertEqual(SETTINGS.find_by_keyword("таксі додому").name, "Транспорт")
        self.assertIsNone(SETTINGS.find_by_keyword("щось незрозуміле"))

    def test_rows_roundtrip_and_bad_rows(self):
        cats, recs = settings_to_rows(SETTINGS)
        cats.append(["???", "Без типу", "", "", ""])          # невідомий тип — пропускаємо
        recs.append(["Зламаний", "сто", "5", "Інше", "так"])   # сума не число — пропускаємо
        recs.append(["Вимкнений", "100", "3", "Інше", "ні"])
        s = settings_from_rows(cats, recs)
        self.assertEqual(len(s.categories), len(SETTINGS.categories))
        self.assertEqual(s.get(EXPENSE, "продукти").limit, 14000)
        self.assertEqual(len(s.recurring), len(SETTINGS.recurring) + 1)
        self.assertFalse(s.recurring[-1].active)

    def test_recurring_key_is_stable_and_short(self):
        r = Recurring("Комуналка", 2600, 20, "Комунальні")
        self.assertEqual(r.key, Recurring("комуналка", 1, 1, "x").key)
        self.assertLessEqual(len(f"payx:{r.key}".encode()), 64)


class DBTest(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")

    def add(self, kind=EXPENSE, amount=100, cat="Продукти", user="Оля", when=T):
        return self.db.add(kind, amount, cat, "", 1, user, when)

    def test_sync_states(self):
        op = self.add()
        self.assertEqual(op.sync, "add")
        self.assertEqual(self.db.update(op.id, amount=150).sync, "add")  # ще не в таблиці
        self.db.mark_synced([op.id])
        self.assertEqual(self.db.update(op.id, comment="АТБ").sync, "update")
        self.db.mark_synced([op.id])
        self.assertEqual(self.db.delete(op.id).sync, "delete")
        self.assertEqual(self.db.last(), [])
        new = self.add()
        self.assertEqual(self.db.delete(new.id).sync, "ok")  # не встиг потрапити в таблицю
        self.assertIsNone(self.db.update(new.id, amount=1))

    def test_update_rejects_unknown_fields(self):
        op = self.add()
        with self.assertRaises(ValueError):
            self.db.update(op.id, user_id=2)

    def test_paid_is_reset_when_operation_deleted(self):
        op = self.add(cat="Зв'язок")
        self.assertTrue(self.db.mark_paid("k", "2026-10", op.id))
        self.assertFalse(self.db.mark_paid("k", "2026-10", op.id))  # повторно — ні
        self.db.delete(op.id)
        self.assertFalse(self.db.is_paid("k", "2026-10"))

    def test_period(self):
        self.add(when=datetime(2026, 9, 30, 23, 59))
        self.add(when=datetime(2026, 10, 1, 0, 0))
        self.assertEqual(len(self.db.period(2026, 10)), 1)


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")
        for kind, amount, cat, user in [(INCOME, 30000, "Зарплата", "Ярослав"), (INCOME, 22000, "Зарплата", "Оля"),
                                        (EXPENSE, 1250, "Продукти", "Оля"), (EXPENSE, 85, "Кафе", "Ярослав"),
                                        (EXPENSE, 3100, "Продукти", "Ярослав"), (EXPENSE, 2600, "Комунальні", "Оля")]:
            self.db.add(kind, amount, cat, "", 1, user, T)
        self.db.add(EXPENSE, 999, "Кафе", "", 1, "Оля", datetime(2026, 9, 5))

    def test_summary(self):
        s = summarize(self.db.period(2026, 10), 2026, 10)
        self.assertEqual((s.income, s.expense, s.balance, s.count), (52000, 7035, 44965, 6))
        self.assertEqual(list(s.expense_by_category), ["Продукти", "Комунальні", "Кафе"])
        self.assertEqual(s.expense_by_user, {"Ярослав": 3185, "Оля": 3850} | {})
        text = format_month(s, SETTINGS, "грн")
        self.assertIn("Жовтень 2026", text)
        self.assertIn("44 965 грн", text)

    def test_history(self):
        hist = history(self.db.since(datetime(2026, 1, 1)))
        self.assertEqual(hist[0], ("2026-10", 52000, 7035))
        self.assertEqual(hist[1], ("2026-09", 0, 999))

    def test_limit_warning_only_on_crossing(self):
        self.assertIsNone(limit_warning("Кафе", 1000, 2000, 3000, "грн"))          # 67%
        self.assertIn("80%", limit_warning("Кафе", 2000, 2400, 3000, "грн"))
        self.assertIsNone(limit_warning("Кафе", 2400, 2600, 3000, "грн"))          # вже попереджали
        self.assertIn("перевищено", limit_warning("Кафе", 2900, 3100, 3000, "грн"))
        self.assertIsNone(limit_warning("Кафе", 3100, 3300, 3000, "грн"))
        self.assertIsNone(limit_warning("Кафе", 0, 9999, None, "грн"))

    def test_shift_month(self):
        self.assertEqual(shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(shift_month(2026, 10, -11), (2025, 11))


class RemindersTest(unittest.TestCase):
    def test_due_date_clamps_to_month_end(self):
        r = Recurring("Оренда", 1000, 31, "Дім")
        self.assertEqual(due_date(r, 2026, 2), date(2026, 2, 28))

    def test_due_reminders(self):
        paid = set()
        is_paid = lambda key, period: (key, period) in paid
        names = lambda today: [r.name for r, _ in due_reminders(SETTINGS, today, is_paid, 2)]
        self.assertEqual(names(date(2026, 10, 3)), ["Садочок", "Інтернет"])        # 1-ше прострочено, 5-те за 2 дні
        self.assertEqual(names(date(2026, 10, 2)), ["Садочок"])
        internet = next(r for r in SETTINGS.recurring if r.name == "Інтернет")
        paid.add((internet.key, "2026-10"))
        self.assertEqual(names(date(2026, 10, 3)), ["Садочок"])
        self.assertIn("Інтернет", names(date(2026, 11, 4)))                        # новий місяць — знову

    def test_texts(self):
        today = date(2026, 10, 3)
        self.assertEqual(when_text(date(2026, 10, 5), today), "до 5 жовтня (через 2 дн.)")
        self.assertEqual(when_text(date(2026, 10, 4), today), "завтра, 4 жовтня")
        self.assertIn("прострочено на 2", when_text(date(2026, 10, 1), today))
        self.assertIn("250 грн", format_reminder(SETTINGS.recurring[1], date(2026, 10, 5), today, "грн"))


# ---------- фейкова Google-таблиця для тестів синхронізації ----------
class FakeWS:
    def __init__(self, title, rows=None):
        self.title, self.rows, self.calls = title, [list(r) for r in (rows or [])], []

    def col_values(self, col):
        return [str(r[col - 1]) for r in self.rows]

    def append_rows(self, rows, value_input_option=None):
        self.calls.append("append")
        self.rows += [list(r) for r in rows]

    def update(self, range_name, values, value_input_option=None):
        self.calls.append(f"update {range_name}")
        start = int("".join(ch for ch in range_name.split(":")[0] if ch.isdigit()))
        for i, row in enumerate(values):
            while len(self.rows) < start + i:
                self.rows.append([])
            self.rows[start - 1 + i] = list(row)

    def delete_rows(self, idx):
        self.calls.append(f"delete {idx}")
        del self.rows[idx - 1]

    def clear(self):
        self.rows = []

    def get_all_values(self):
        return [[str(c) for c in r] for r in self.rows]

    def format(self, *a, **k):
        pass


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = []

    def worksheets(self):
        return self.sheets

    def add_worksheet(self, title, rows, cols):
        ws = FakeWS(title)
        self.sheets.append(ws)
        return ws


class SheetsTest(unittest.TestCase):
    def setUp(self):
        self.sh = FakeSpreadsheet()
        self.sync = SheetsSync(self.sh)
        self.sync.ensure_structure(SETTINGS)
        self.db = DB(":memory:")
        self.ws = self.sync._ws(OPS)

    def push(self):
        self.db.mark_synced(self.sync.push(self.db.pending()))

    def test_structure_and_settings_from_sheet(self):
        self.assertEqual([w.title for w in self.sh.worksheets()], [OPS, DASHBOARD, "Категорії", "Регулярні платежі"])
        self.sync.ensure_structure(SETTINGS)  # повторно нічого не дублює
        self.assertEqual(len(self.sh.worksheets()), 4)
        s = self.sync.load_settings()
        self.assertEqual(s.find_by_keyword("кава").name, "Кафе")
        self.assertEqual(len(s.recurring), len(SETTINGS.recurring))

    def test_add_update_delete(self):
        a = self.db.add(EXPENSE, 100, "Продукти", "АТБ", 1, "Оля", T)
        b = self.db.add(EXPENSE, 85, "Кафе", "кава", 1, "Ярослав", T)
        c = self.db.add(INCOME, 30000, "Зарплата", "", 1, "Ярослав", T)
        self.push()
        self.assertEqual(self.ws.col_values(1), ["ID", "1", "2", "3"])
        self.assertEqual(self.db.pending(), [])

        self.db.update(b.id, amount=95)
        self.db.delete(a.id)
        self.push()
        self.assertEqual(self.ws.col_values(1), ["ID", "2", "3"])
        self.assertEqual(self.ws.rows[1][4], 95)
        self.assertEqual(self.ws.rows[2][2], "Дохід")
        self.assertEqual(self.ws.rows[1][7], "2026-10")

    def test_retry_does_not_duplicate(self):
        self.db.add(EXPENSE, 100, "Продукти", "", 1, "Оля", T)
        self.sync.push(self.db.pending())  # дані пішли, але позначити не встигли (наприклад, бот впав)
        self.push()
        self.assertEqual(self.ws.col_values(1), ["ID", "1"])

    def test_dashboard(self):
        self.db.add(INCOME, 1000, "Зарплата", "", 1, "Оля", T)
        self.db.add(EXPENSE, 2500, "Кафе", "", 1, "Оля", T)
        ops = self.db.since(datetime(2026, 1, 1))
        m = dashboard_matrix(summarize(ops, 2026, 10), history(ops), SETTINGS, T)
        self.sync.write_dashboard(m)
        flat = {r[0]: r[1:] for r in self.sync._ws(DASHBOARD).rows if r and r[0]}
        self.assertEqual(flat["Залишок"][0], -1500)
        self.assertEqual(flat["Кафе"][:3], [2500, 3000, "83%"])
        self.assertEqual(flat["Продукти"][:3], [0.0, 14000, "0%"])   # категорія з лімітом показується навіть без витрат
        self.assertEqual(flat["2026-10"], [1000, 2500, -1500])
        self.assertTrue(all(len(r) == 4 for r in m))


if __name__ == "__main__":
    unittest.main()
