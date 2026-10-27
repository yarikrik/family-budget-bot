"""Нагадування про регулярні платежі (інтернет, комуналка, садочок...)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import partial
from html import escape as _escape
from typing import Callable

from .money import fmt_money
from .reports import MONTHS, human_date
from .settings import Recurring, Settings

escape = partial(_escape, quote=False)  # апостроф у «Зв'язок» не перетворюємо на &#x27;


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def due_date(rec: Recurring, year: int, month: int) -> date:
    """День 31 у місяці з 30 днями → останній день місяця."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(rec.day, last))


def due_reminders(settings: Settings, today: date, is_paid: Callable[[str, str], bool],
                  days_before: int = 2) -> list[tuple[Recurring, date]]:
    """Платежі цього місяця, які ще не оплачені і до яких лишилось ≤ days_before днів (або вже прострочені)."""
    out = []
    for rec in settings.recurring:
        if not rec.active:
            continue
        due = due_date(rec, today.year, today.month)
        if today >= due - timedelta(days=days_before) and not is_paid(rec.key, period_of(today)):
            out.append((rec, due))
    return sorted(out, key=lambda x: x[1])


def when_text(due: date, today: date) -> str:
    days = (due - today).days
    if days > 1:
        return f"до {human_date(due)} (через {days} дн.)"
    if days == 1:
        return f"завтра, {human_date(due)}"
    if days == 0:
        return f"сьогодні, {human_date(due)}"
    return f"⚠️ прострочено на {-days} дн. (було {human_date(due)})"


def format_reminder(rec: Recurring, due: date, today: date, currency: str) -> str:
    return (f"🔔 <b>Нагадування про платіж</b>\n\n"
            f"<b>{escape(rec.name)}</b> — {fmt_money(rec.amount, currency)}\n"
            f"📅 {when_text(due, today)}\n\n"
            f"Після оплати натисніть кнопку — бот запише витрату в бюджет.")


def format_recurring_list(settings: Settings, today: date, paid_by: Callable[[str], str | None],
                          currency: str) -> str:
    if not settings.recurring:
        return "Регулярних платежів немає. Додайте їх в аркуш «Регулярні платежі» або settings.json."
    lines = [f"🔁 <b>Регулярні платежі — {MONTHS[today.month - 1].lower()}</b>", ""]
    for rec in sorted(settings.recurring, key=lambda r: r.day):
        if not rec.active:
            continue
        due = due_date(rec, today.year, today.month)
        who = paid_by(rec.key)
        status = f"✅ оплачено ({escape(who)})" if who else ("⏳ " + when_text(due, today) if due >= today
                                                              else when_text(due, today))
        lines.append(f"<b>{escape(rec.name)}</b> — {fmt_money(rec.amount, currency)}, {rec.day} числа\n    {status}")
    return "\n".join(lines)
