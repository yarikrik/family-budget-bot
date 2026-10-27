from __future__ import annotations

from functools import partial
from html import escape as _escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from . import keyboards as kb
from .config import Config
from .db import DB, Operation
from .money import fmt_money, parse_amount, parse_quick
from .reminders import format_recurring_list, period_of
from .reports import format_last, format_month, format_operation, limit_warning, summarize
from .services import Store
from .settings import EXPENSE, INCOME, KIND_LABEL

escape = partial(_escape, quote=False)  # апостроф у «Зв'язок» не перетворюємо на &#x27;

router = Router()

# текст, введений у діалозі (не кнопка меню і не команда — ті працюють з будь-якого кроку)
INPUT = F.text & ~F.text.in_(kb.MENU) & ~F.text.startswith("/")

HELP = (
    "👋 Я веду сімейний бюджет.\n\n"
    "<b>Швидкий запис</b> — просто напишіть суму і що купили:\n"
    "<code>кава 85</code>, <code>1250 АТБ</code>, <code>таксі 120+95</code>\n"
    "Якщо слово знайоме (АТБ, таксі, аптека…), категорію підставлю сам.\n\n"
    "Або натисніть «➖ Витрата» / «➕ Дохід» і оберіть категорію.\n"
    "Кожен запис можна змінити чи видалити кнопками під ним.\n\n"
    "/report — звіт за місяць, /last — останні записи,\n"
    "/payments — регулярні платежі, /reload — оновити категорії з таблиці, /cancel — скасувати"
)


class Add(StatesGroup):
    category = State()
    amount = State()
    comment = State()


class Edit(StatesGroup):
    amount = State()
    comment = State()


class PayOther(StatesGroup):
    amount = State()


async def _safe_edit(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:  # «message is not modified» при повторному натисканні
        if "not modified" not in str(e):
            raise


async def _create(user: User, kind: str, amount: float, category: str, comment: str,
                  db: DB, store: Store, cfg: Config) -> tuple[Operation, str]:
    """Зберігає операцію і повертає текст картки (з попередженням про ліміт, якщо треба)."""
    now = cfg.now()
    spent_before = sum(o.amount for o in db.period(now.year, now.month) if o.kind == kind and o.category == category)
    op = db.add(kind, amount, category, comment, user.id, cfg.name_of(user.id, user.first_name), now)
    store.touch()
    spent = store.month_spent(db, op)
    text = format_operation(op, store.settings, cfg.currency, spent, header="✅ <b>Записано</b>")
    cat = store.settings.get(kind, category)
    if kind == EXPENSE and cat:
        warn = limit_warning(cat.name, spent_before, spent, cat.limit, cfg.currency)
        if warn:
            text += f"\n\n{warn}"
    return op, text


# ---------- старт, допомога, скасування ----------

@router.message(CommandStart())
@router.message(Command("help"))
async def start(m: Message, state: FSMContext, cfg: Config):
    await state.clear()
    name = escape(cfg.name_of(m.from_user.id, m.from_user.first_name))
    await m.answer(f"Привіт, {name}!\n\n{HELP}", reply_markup=kb.main_kb())


@router.message(Command("cancel"))
async def cancel_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Скасовано.", reply_markup=kb.main_kb())


@router.callback_query(F.data == "cancel")
async def cancel_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit(c.message, "Скасовано.")
    await c.answer()


# ---------- додавання через кнопки ----------

@router.message(F.text.in_({kb.BTN_EXPENSE, kb.BTN_INCOME}))
async def choose_kind(m: Message, state: FSMContext, store: Store):
    kind = EXPENSE if m.text == kb.BTN_EXPENSE else INCOME
    await state.clear()
    await state.set_state(Add.category)
    await state.update_data(kind=kind)
    await m.answer(f"{'➖' if kind == EXPENSE else '➕'} <b>{KIND_LABEL[kind]}</b> — оберіть категорію:",
                   reply_markup=kb.categories_kb(store.settings.by_kind(kind), "cat", kind))


@router.callback_query(Add.category, F.data.startswith("kind:"))
async def switch_kind(c: CallbackQuery, state: FSMContext, store: Store, cfg: Config):
    kind = c.data.split(":")[1]
    data = await state.update_data(kind=kind)
    head = f"{fmt_money(data['amount'], cfg.currency)}" + (f" — {escape(data['comment'])}" if data.get("comment") else "")
    await _safe_edit(c.message, f"{'➕' if kind == INCOME else '💸'} {head}\nОберіть категорію:",
                     kb.categories_kb(store.settings.by_kind(kind), "cat", kind, allow_switch=True))
    await c.answer()


@router.callback_query(Add.category, F.data.startswith("cat:"))
async def picked_category(c: CallbackQuery, state: FSMContext, store: Store, db: DB, cfg: Config):
    data = await state.get_data()
    kind = data["kind"]
    cats = store.settings.by_kind(kind)
    i = int(c.data.split(":")[1])
    if i >= len(cats):
        await c.answer("Список категорій оновився — оберіть ще раз", show_alert=True)
        await _safe_edit(c.message, "Оберіть категорію:", kb.categories_kb(cats, "cat", kind))
        return
    cat = cats[i]
    if data.get("amount"):  # швидкий запис: сума вже відома
        await state.clear()
        op, text = await _create(c.from_user, kind, data["amount"], cat.name, data.get("comment", ""), db, store, cfg)
        await _safe_edit(c.message, text, kb.op_kb(op))
    else:
        await state.update_data(category=cat.name)
        await state.set_state(Add.amount)
        await _safe_edit(c.message, f"{escape(cat.label)} — введіть суму:\n<i>можна так: 120+85</i>", kb.cancel_kb())
    await c.answer()


@router.message(Add.amount, INPUT)
async def got_amount(m: Message, state: FSMContext):
    amount = parse_amount(m.text)
    if amount is None:
        await m.answer("Не схоже на суму 🤔 Приклад: <code>250</code>, <code>1 250,50</code> або <code>120+85</code>")
        return
    await state.update_data(amount=amount)
    await state.set_state(Add.comment)
    await m.answer("💬 Коментар? Напишіть (наприклад, «АТБ») або натисніть кнопку.", reply_markup=kb.skip_comment_kb())


@router.message(Add.comment, INPUT)
async def got_comment(m: Message, state: FSMContext, store: Store, db: DB, cfg: Config):
    data = await state.get_data()
    await state.clear()
    op, text = await _create(m.from_user, data["kind"], data["amount"], data["category"], m.text[:200], db, store, cfg)
    await m.answer(text, reply_markup=kb.op_kb(op))


@router.callback_query(Add.comment, F.data == "skip")
async def skip_comment(c: CallbackQuery, state: FSMContext, store: Store, db: DB, cfg: Config):
    data = await state.get_data()
    await state.clear()
    op, text = await _create(c.from_user, data["kind"], data["amount"], data["category"], "", db, store, cfg)
    await _safe_edit(c.message, text, kb.op_kb(op))
    await c.answer()


# ---------- картка запису: змінити / видалити ----------

async def _get_op(c: CallbackQuery, db: DB) -> Operation | None:
    op = db.get(int(c.data.rsplit(":", 1)[1]))
    if op is None or op.deleted:
        await c.answer("Запис уже видалено", show_alert=True)
        return None
    return op


@router.callback_query(F.data.startswith("op:show:"))
async def op_show(c: CallbackQuery, state: FSMContext, db: DB, store: Store, cfg: Config):
    await state.clear()
    if op := await _get_op(c, db):
        text = format_operation(op, store.settings, cfg.currency, store.month_spent(db, op))
        if c.message.text and c.message.text.startswith("🧾 Останні"):
            await c.message.answer(text, reply_markup=kb.op_kb(op))  # список лишаємо на місці
        else:
            await _safe_edit(c.message, text, kb.op_kb(op))
        await c.answer()


@router.callback_query(F.data.startswith("op:amt:"))
async def op_amount(c: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    if op := await _get_op(c, db):
        await state.set_state(Edit.amount)
        await state.update_data(op_id=op.id)
        await c.message.answer(f"Нова сума для запису #{op.id} (зараз {fmt_money(op.amount, cfg.currency)}):",
                               reply_markup=kb.cancel_kb())
        await c.answer()


@router.message(Edit.amount, INPUT)
async def op_amount_set(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    amount = parse_amount(m.text)
    if amount is None:
        await m.answer("Не схоже на суму 🤔 Спробуйте ще раз або /cancel")
        return
    op_id = (await state.get_data())["op_id"]
    await state.clear()
    op = db.update(op_id, amount=amount)
    if op is None:
        await m.answer("Запис уже видалено.")
        return
    store.touch()
    await m.answer(format_operation(op, store.settings, cfg.currency, store.month_spent(db, op),
                                    header="✏️ <b>Суму змінено</b>"), reply_markup=kb.op_kb(op))


@router.callback_query(F.data.startswith("op:com:"))
async def op_comment(c: CallbackQuery, state: FSMContext, db: DB):
    if op := await _get_op(c, db):
        await state.set_state(Edit.comment)
        await state.update_data(op_id=op.id)
        await c.message.answer(f"Новий коментар для запису #{op.id} («-» — прибрати коментар):",
                               reply_markup=kb.cancel_kb())
        await c.answer()


@router.message(Edit.comment, INPUT)
async def op_comment_set(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    op_id = (await state.get_data())["op_id"]
    await state.clear()
    comment = "" if m.text.strip() == "-" else m.text.strip()[:200]
    op = db.update(op_id, comment=comment)
    if op is None:
        await m.answer("Запис уже видалено.")
        return
    store.touch()
    await m.answer(format_operation(op, store.settings, cfg.currency, store.month_spent(db, op),
                                    header="✏️ <b>Коментар змінено</b>"), reply_markup=kb.op_kb(op))


@router.callback_query(F.data.startswith("op:cat:"))
async def op_category(c: CallbackQuery, db: DB, store: Store):
    if op := await _get_op(c, db):
        cats = store.settings.by_kind(op.kind)
        await _safe_edit(c.message, f"Нова категорія для запису #{op.id}:",
                         kb.categories_kb(cats, f"setcat:{op.id}", op.kind))
        await c.answer()


@router.callback_query(F.data.startswith("setcat:"))
async def op_category_set(c: CallbackQuery, db: DB, store: Store, cfg: Config):
    _, op_id, i = c.data.split(":")
    op = db.get(int(op_id))
    cats = store.settings.by_kind(op.kind) if op else []
    if op is None or op.deleted or int(i) >= len(cats):
        await c.answer("Не вдалося змінити — спробуйте ще раз", show_alert=True)
        return
    op = db.update(op.id, category=cats[int(i)].name)
    store.touch()
    await _safe_edit(c.message, format_operation(op, store.settings, cfg.currency, store.month_spent(db, op),
                                                 header="🏷 <b>Категорію змінено</b>"), kb.op_kb(op))
    await c.answer()


@router.callback_query(F.data.startswith("op:del:"))
async def op_delete(c: CallbackQuery, db: DB, store: Store, cfg: Config):
    if op := await _get_op(c, db):
        text = format_operation(op, store.settings, cfg.currency) + "\n\n<b>Видалити цей запис?</b>"
        await _safe_edit(c.message, text, kb.confirm_delete_kb(op.id))
        await c.answer()


@router.callback_query(F.data.startswith("op:delok:"))
async def op_delete_ok(c: CallbackQuery, db: DB, store: Store, cfg: Config):
    if op := await _get_op(c, db):
        db.delete(op.id)
        store.touch()
        who = escape(cfg.name_of(c.from_user.id, c.from_user.first_name))
        body = format_operation(op, store.settings, cfg.currency, header=f"🗑 <b>Видалено</b> ({who})")
        await _safe_edit(c.message, body)
        await c.answer("Видалено")


# ---------- звіти ----------

@router.message(Command("report"))
@router.message(F.text == kb.BTN_REPORT)
async def report(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    await state.clear()
    now = cfg.now()
    s = summarize(db.period(now.year, now.month), now.year, now.month)
    await m.answer(format_month(s, store.settings, cfg.currency), reply_markup=kb.month_kb(now.year, now.month, False))


@router.callback_query(F.data.startswith("rep:"))
async def report_month(c: CallbackQuery, db: DB, store: Store, cfg: Config):
    year, month = map(int, c.data[4:].split("-"))
    now = cfg.now()
    s = summarize(db.period(year, month), year, month)
    has_next = (year, month) < (now.year, now.month)
    await _safe_edit(c.message, format_month(s, store.settings, cfg.currency), kb.month_kb(year, month, has_next))
    await c.answer()


@router.message(Command("last"))
@router.message(F.text == kb.BTN_LAST)
async def last(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    await state.clear()
    ops = db.last(10)
    await m.answer(format_last(ops, store.settings, cfg.currency), reply_markup=kb.last_kb(ops) if ops else None)


# ---------- регулярні платежі ----------

def _paid_by(db: DB, period: str):
    def who(key: str) -> str | None:
        op = db.paid_op(key, period)
        return op.user_name if op else None
    return who


@router.message(Command("payments"))
@router.message(F.text == kb.BTN_RECURRING)
async def payments(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    await state.clear()
    today = cfg.now().date()
    period = period_of(today)
    unpaid = [r for r in store.settings.recurring if r.active and not db.is_paid(r.key, period)]
    await m.answer(format_recurring_list(store.settings, today, _paid_by(db, period), cfg.currency),
                   reply_markup=kb.recurring_kb(sorted(unpaid, key=lambda r: r.day)))


async def _pay(user: User, key: str, amount: float | None, db: DB, store: Store, cfg: Config) -> str | None:
    """Записує оплату регулярного платежу. Повертає текст відповіді або None, якщо вже оплачено."""
    rec = store.settings.recurring_by_key(key)
    if rec is None:
        return "Цього платежу вже немає в налаштуваннях."
    period = period_of(cfg.now().date())
    if db.is_paid(key, period):
        return None
    op, text = await _create(user, EXPENSE, amount or rec.amount, rec.category,
                             f"{rec.name} (регулярний платіж)", db, store, cfg)
    if not db.mark_paid(key, period, op.id):  # хтось натиснув одночасно
        db.delete(op.id)
        return None
    return text.replace("✅ <b>Записано</b>", f"✅ <b>Оплачено: {escape(rec.name)}</b>")


@router.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery, db: DB, store: Store, cfg: Config):
    key = c.data[4:]
    text = await _pay(c.from_user, key, None, db, store, cfg)
    if text is None:
        op = db.paid_op(key, period_of(cfg.now().date()))
        await c.answer(f"Вже оплачено ({op.user_name if op else 'хтось із сім`ї'})", show_alert=True)
        return
    await c.message.answer(text)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await c.answer("Записано ✅")


@router.callback_query(F.data.startswith("payx:"))
async def pay_other(c: CallbackQuery, state: FSMContext, db: DB, store: Store, cfg: Config):
    key = c.data[5:]
    rec = store.settings.recurring_by_key(key)
    if rec is None or db.is_paid(key, period_of(cfg.now().date())):
        await c.answer("Вже оплачено або платіж видалено", show_alert=True)
        return
    await state.set_state(PayOther.amount)
    await state.update_data(key=key)
    await c.message.answer(f"Скільки сплатили за «{escape(rec.name)}»?", reply_markup=kb.cancel_kb())
    await c.answer()


@router.message(PayOther.amount, INPUT)
async def pay_other_amount(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    amount = parse_amount(m.text)
    if amount is None:
        await m.answer("Не схоже на суму 🤔 Спробуйте ще раз або /cancel")
        return
    key = (await state.get_data())["key"]
    await state.clear()
    text = await _pay(m.from_user, key, amount, db, store, cfg)
    await m.answer(text or "Цей платіж уже оплатив хтось інший.")


# ---------- службове ----------

@router.message(Command("reload"))
async def reload_settings(m: Message, store: Store):
    s = await store.reload()
    await m.answer(f"🔄 Налаштування оновлено: {len(s.categories)} категорій, {len(s.recurring)} регулярних платежів.")


@router.message(StateFilter(None), F.text)
async def quick_entry(m: Message, state: FSMContext, db: DB, store: Store, cfg: Config):
    """Будь-яке повідомлення без активного діалогу пробуємо прочитати як «кава 85»."""
    parsed = parse_quick(m.text)
    if not parsed:
        await m.answer("Не зрозумів 🙂 Напишіть суму і що купили, наприклад <code>кава 85</code>, "
                       "або скористайтесь кнопками.", reply_markup=kb.main_kb())
        return
    amount, comment = parsed
    cat = store.settings.find_by_keyword(comment) if comment else None
    if cat:
        op, text = await _create(m.from_user, EXPENSE, amount, cat.name, comment, db, store, cfg)
        await m.answer(text, reply_markup=kb.op_kb(op))
        return
    await state.set_state(Add.category)
    await state.update_data(kind=EXPENSE, amount=amount, comment=comment)
    head = fmt_money(amount, cfg.currency) + (f" — {escape(comment)}" if comment else "")
    await m.answer(f"💸 {head}\nОберіть категорію:",
                   reply_markup=kb.categories_kb(store.settings.by_kind(EXPENSE), "cat", EXPENSE, allow_switch=True))
