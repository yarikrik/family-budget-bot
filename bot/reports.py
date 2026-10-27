"""Підсумки за місяць і тексти повідомлень."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from html import escape as _escape

from .db import Operation
from .money import fmt_money
from .settings import EXPENSE, INCOME, KIND_LABEL, Settings

escape = partial(_escape, quote=False)  # апостроф у «Зв'язок» не перетворюємо на &#x27;

MONTHS = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
          "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]


@dataclass
class MonthSummary:
    year: int
    month: int
    income: float = 0.0
    expense: float = 0.0
    expense_by_category: dict[str, float] = field(default_factory=dict)
    income_by_category: dict[str, float] = field(default_factory=dict)
    expense_by_user: dict[str, float] = field(default_factory=dict)
    count: int = 0

    @property
    def balance(self) -> float:
        return round(self.income - self.expense, 2)

    @property
    def title(self) -> str:
        return f"{MONTHS[self.month - 1]} {self.year}"


def _sorted(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 2) for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))}


def summarize(ops: list[Operation], year: int, month: int) -> MonthSummary:
    s = MonthSummary(year, month)
    exp_cat, inc_cat, exp_user = defaultdict(float), defaultdict(float), defaultdict(float)
    for op in ops:
        if op.deleted or (op.created_at.year, op.created_at.month) != (year, month):
            continue
        s.count += 1
        if op.kind == INCOME:
            s.income += op.amount
            inc_cat[op.category] += op.amount
        else:
            s.expense += op.amount
            exp_cat[op.category] += op.amount
            exp_user[op.user_name] += op.amount
    s.income, s.expense = round(s.income, 2), round(s.expense, 2)
    s.expense_by_category, s.income_by_category = _sorted(exp_cat), _sorted(inc_cat)
    s.expense_by_user = _sorted(exp_user)
    return s


def history(ops: list[Operation]) -> list[tuple[str, float, float]]:
    """[('2026-10', доходи, витрати), ...] від найновішого місяця."""
    agg: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for op in ops:
        if not op.deleted:
            agg[op.period][0 if op.kind == INCOME else 1] += op.amount
    return [(p, round(i, 2), round(e, 2)) for p, (i, e) in sorted(agg.items(), reverse=True)]


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = year * 12 + (month - 1) + delta
    return m // 12, m % 12 + 1


def progress_bar(pct: float, width: int = 8) -> str:
    filled = min(width, max(0, round(pct / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def limit_status(spent: float, limit: float) -> str:
    pct = spent / limit * 100 if limit else 0
    mark = " 🔴" if pct > 100 else (" ⚠️" if pct >= 80 else "")
    return f"{progress_bar(pct)} {pct:.0f}%{mark}"


def short_dt(dt: datetime) -> str:
    return dt.strftime("%d.%m %H:%M")


def format_operation(op: Operation, settings: Settings, currency: str, month_spent: float | None = None,
                     header: str | None = None) -> str:
    sign = "−" if op.kind == EXPENSE else "+"
    lines = [header or f"🧾 <b>Запис #{op.id}</b>",
             f"{'➖' if op.kind == EXPENSE else '➕'} {KIND_LABEL[op.kind]} · {escape(settings.label(op.kind, op.category))}",
             f"💵 <b>{sign}{fmt_money(op.amount, currency)}</b>"]
    if op.comment:
        lines.append(f"💬 {escape(op.comment)}")
    lines.append(f"👤 {escape(op.user_name)} · {short_dt(op.created_at)}")
    cat = settings.get(op.kind, op.category)
    if op.kind == EXPENSE and month_spent is not None:
        month = MONTHS[op.created_at.month - 1].lower()
        if cat and cat.limit:
            lines.append(f"\n📊 {escape(cat.name)} за {month}: {fmt_money(month_spent, currency)} з "
                         f"{fmt_money(cat.limit, currency)}\n{limit_status(month_spent, cat.limit)}")
        else:
            lines.append(f"\n📊 {escape(op.category)} за {month}: {fmt_money(month_spent, currency)}")
    return "\n".join(lines)


def limit_warning(category: str, spent_before: float, spent_after: float, limit: float | None,
                  currency: str) -> str | None:
    """Попередження тільки в момент перетину порогу 80% або 100%, щоб не спамити на кожну покупку."""
    if not limit:
        return None
    if spent_before <= limit < spent_after:
        return (f"🔴 Ліміт на «{escape(category)}» перевищено: {fmt_money(spent_after, currency)} "
                f"з {fmt_money(limit, currency)} (+{fmt_money(spent_after - limit, currency)})")
    if spent_before < limit * 0.8 <= spent_after <= limit:
        return (f"⚠️ «{escape(category)}»: витрачено {spent_after / limit * 100:.0f}% місячного ліміту, "
                f"лишилось {fmt_money(limit - spent_after, currency)}")
    return None


def format_month(s: MonthSummary, settings: Settings, currency: str) -> str:
    lines = [f"📊 <b>{s.title}</b>", ""]
    if not s.count:
        return "\n".join(lines + ["Записів за цей місяць немає."])
    lines += [f"➕ Доходи: <b>{fmt_money(s.income, currency)}</b>",
              f"➖ Витрати: <b>{fmt_money(s.expense, currency)}</b>",
              f"{'💰' if s.balance >= 0 else '🔻'} Залишок: <b>{fmt_money(s.balance, currency)}</b>"]
    if s.expense_by_category:
        lines += ["", "<b>Витрати за категоріями</b>"]
        for name, spent in s.expense_by_category.items():
            share = spent / s.expense * 100 if s.expense else 0
            line = f"{escape(settings.label(EXPENSE, name))} — {fmt_money(spent, currency)} ({share:.0f}%)"
            cat = settings.get(EXPENSE, name)
            if cat and cat.limit:
                line += f"\n      ліміт {fmt_money(cat.limit, currency)} {limit_status(spent, cat.limit)}"
            lines.append(line)
    if s.income_by_category:
        lines += ["", "<b>Доходи</b>"]
        lines += [f"{escape(settings.label(INCOME, n))} — {fmt_money(v, currency)}"
                  for n, v in s.income_by_category.items()]
    if len(s.expense_by_user) > 1:
        lines += ["", "<b>Хто скільки витратив</b>"]
        lines += [f"👤 {escape(n)} — {fmt_money(v, currency)}" for n, v in s.expense_by_user.items()]
    lines += ["", f"<i>Записів: {s.count}</i>"]
    return "\n".join(lines)


def format_last(ops: list[Operation], settings: Settings, currency: str) -> str:
    if not ops:
        return "Записів ще немає. Надішліть, наприклад: <code>кава 85</code>"
    lines = ["🧾 <b>Останні записи</b>", ""]
    for i, op in enumerate(ops, 1):
        sign = "−" if op.kind == EXPENSE else "+"
        comment = f" · {escape(op.comment)}" if op.comment else ""
        lines.append(f"{i}. {short_dt(op.created_at)} {escape(settings.label(op.kind, op.category))} "
                     f"<b>{sign}{fmt_money(op.amount, currency)}</b>{comment} · {escape(op.user_name)}")
    lines += ["", "Натисніть номер, щоб змінити або видалити запис."]
    return "\n".join(lines)


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"
