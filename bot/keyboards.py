from __future__ import annotations

from aiogram.types import InlineKeyboardButton as IB
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .db import Operation
from .settings import EXPENSE, INCOME, Category, Recurring

BTN_EXPENSE = "➖ Витрата"
BTN_INCOME = "➕ Дохід"
BTN_REPORT = "📊 Звіт за місяць"
BTN_LAST = "🧾 Останні записи"
BTN_RECURRING = "🔁 Регулярні платежі"
MENU = {BTN_EXPENSE, BTN_INCOME, BTN_REPORT, BTN_LAST, BTN_RECURRING}


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_EXPENSE), KeyboardButton(text=BTN_INCOME)],
                  [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_LAST)],
                  [KeyboardButton(text=BTN_RECURRING)]],
        resize_keyboard=True, input_field_placeholder="Наприклад: кава 85")


def _grid(buttons: list[IB], cols: int) -> list[list[IB]]:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def categories_kb(categories: list[Category], prefix: str, kind: str, allow_switch: bool = False) -> InlineKeyboardMarkup:
    rows = _grid([IB(text=c.label, callback_data=f"{prefix}:{i}") for i, c in enumerate(categories)], 2)
    if allow_switch:
        other = INCOME if kind == EXPENSE else EXPENSE
        rows.append([IB(text="🔄 Це дохід" if other == INCOME else "🔄 Це витрата", callback_data=f"kind:{other}")])
    rows.append([IB(text="✖️ Скасувати", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_comment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[IB(text="Без коментаря ➡️", callback_data="skip")],
                                                 [IB(text="✖️ Скасувати", callback_data="cancel")]])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[IB(text="✖️ Скасувати", callback_data="cancel")]])


def op_kb(op: Operation) -> InlineKeyboardMarkup:
    i = op.id
    return InlineKeyboardMarkup(inline_keyboard=[
        [IB(text="✏️ Сума", callback_data=f"op:amt:{i}"), IB(text="🏷 Категорія", callback_data=f"op:cat:{i}")],
        [IB(text="💬 Коментар", callback_data=f"op:com:{i}"), IB(text="🗑 Видалити", callback_data=f"op:del:{i}")],
    ])


def confirm_delete_kb(op_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        IB(text="🗑 Так, видалити", callback_data=f"op:delok:{op_id}"),
        IB(text="↩️ Ні", callback_data=f"op:show:{op_id}")]])


def last_kb(ops: list[Operation]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_grid(
        [IB(text=str(n), callback_data=f"op:show:{op.id}") for n, op in enumerate(ops, 1)], 5))


def month_kb(year: int, month: int, has_next: bool) -> InlineKeyboardMarkup:
    py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
    ny, nm = (year, month + 1) if month < 12 else (year + 1, 1)
    row = [IB(text="◀️", callback_data=f"rep:{py}-{pm:02d}")]
    if has_next:
        row.append(IB(text="▶️", callback_data=f"rep:{ny}-{nm:02d}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def pay_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        IB(text="✅ Оплачено", callback_data=f"pay:{key}"),
        IB(text="✏️ Інша сума", callback_data=f"payx:{key}")]])


def recurring_kb(unpaid: list[Recurring]) -> InlineKeyboardMarkup | None:
    if not unpaid:
        return None
    return InlineKeyboardMarkup(inline_keyboard=_grid(
        [IB(text=f"✅ {r.name}", callback_data=f"pay:{r.key}") for r in unpaid], 2))
