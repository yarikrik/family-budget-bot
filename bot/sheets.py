"""Синхронізація з Google Sheets.

Аркуші:
  «Операції»          — усі записи (бот додає, змінює і видаляє рядки за ID)
  «Дашборд»           — підсумки поточного місяця та історія по місяцях (бот перераховує після змін)
  «Категорії»         — категорії, емодзі, ліміти, ключові слова (редагуються вручну)
  «Регулярні платежі» — назва, сума, день місяця (редагуються вручну)

gspread синхронний, тому всі методи викликаються через asyncio.to_thread.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .db import Operation
from .reports import MonthSummary
from .settings import EXPENSE, KIND_LABEL, Settings, settings_from_rows, settings_to_rows

log = logging.getLogger(__name__)

OPS, DASHBOARD, CATEGORIES, RECURRING = "Операції", "Дашборд", "Категорії", "Регулярні платежі"
OPS_HEADER = ["ID", "Дата", "Тип", "Категорія", "Сума", "Коментар", "Хто", "Місяць"]


def op_row(op: Operation) -> list:
    return [op.id, op.created_at.strftime("%Y-%m-%d %H:%M"), KIND_LABEL[op.kind], op.category,
            op.amount, op.comment, op.user_name, op.period]


def dashboard_matrix(s: MonthSummary, hist: list[tuple[str, float, float]], settings: Settings,
                     updated: datetime) -> list[list]:
    rows: list[list] = [
        [f"Сімейний бюджет — {s.title}", "", "", ""],
        ["Оновлено", updated.strftime("%d.%m.%Y %H:%M"), "", ""],
        [],
        ["Доходи", s.income], ["Витрати", s.expense], ["Залишок", s.balance],
        [],
        ["Категорія", "Витрачено", "Ліміт", "% ліміту"],
    ]
    names = list(s.expense_by_category) + [c.name for c in settings.by_kind(EXPENSE)
                                           if c.name not in s.expense_by_category and c.limit]
    for name in names:
        spent = s.expense_by_category.get(name, 0.0)
        cat = settings.get(EXPENSE, name)
        limit = cat.limit if cat and cat.limit else ""
        pct = f"{spent / limit * 100:.0f}%" if limit else ""
        rows.append([name, spent, limit, pct])
    rows += [[], ["Хто", "Витрачено"]] + [[n, v] for n, v in s.expense_by_user.items()]
    rows += [[], ["Місяць", "Доходи", "Витрати", "Залишок"]]
    rows += [[p, i, e, round(i - e, 2)] for p, i, e in hist[:12]]
    width = 4
    return [r + [""] * (width - len(r)) for r in rows]


class SheetsSync:
    def __init__(self, spreadsheet):
        self.sh = spreadsheet

    @classmethod
    def connect(cls, credentials: Path, sheet_id: str) -> "SheetsSync":
        import gspread  # імпорт тут, щоб бот і тести працювали без Google
        gc = gspread.service_account(filename=str(credentials))
        return cls(gc.open_by_key(sheet_id))

    def _ws(self, title: str):
        return next((w for w in self.sh.worksheets() if w.title == title), None)

    def ensure_structure(self, defaults: Settings) -> None:
        """Створює відсутні аркуші. Налаштування заповнюються з settings.json лише при першому запуску."""
        cat_rows, rec_rows = settings_to_rows(defaults)
        for title, rows, cols in ((OPS, [OPS_HEADER], 8), (DASHBOARD, [], 4),
                                  (CATEGORIES, cat_rows, 5), (RECURRING, rec_rows, 5)):
            if self._ws(title) is None:
                ws = self.sh.add_worksheet(title=title, rows=max(100, len(rows) + 50), cols=cols)
                if rows:
                    ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")
                log.info("Створено аркуш «%s»", title)

    def load_settings(self) -> Settings:
        return settings_from_rows(self._ws(CATEGORIES).get_all_values(), self._ws(RECURRING).get_all_values())

    def push(self, ops: list[Operation]) -> list[int]:
        """Застосовує зміни до аркуша «Операції». Повертає ID, які тепер синхронізовані.

        Повторний виклик безпечний: якщо рядок з таким ID уже є, він оновлюється, а не дублюється.
        """
        ws = self._ws(OPS)
        ids = ws.col_values(1)
        row_of = {v: i for i, v in enumerate(ids, start=1) if i > 1}
        appends, deletes, done = [], [], []
        for op in ops:
            row = row_of.get(str(op.id))
            if op.sync == "delete" or op.deleted:
                if row:
                    deletes.append(row)
            elif row:
                ws.update(range_name=f"A{row}:H{row}", values=[op_row(op)], value_input_option="USER_ENTERED")
            else:
                appends.append(op_row(op))
            done.append(op.id)
        if appends:
            ws.append_rows(appends, value_input_option="USER_ENTERED")
        for row in sorted(deletes, reverse=True):  # знизу вгору, щоб номери рядків не зсувались
            ws.delete_rows(row)
        return done

    def write_dashboard(self, matrix: list[list]) -> None:
        ws = self._ws(DASHBOARD)
        ws.clear()
        ws.update(range_name="A1", values=matrix, value_input_option="USER_ENTERED")
        try:
            ws.format("A1", {"textFormat": {"bold": True, "fontSize": 14}})
        except Exception:  # оформлення — не критично
            pass
