"""Розбір сум, швидкого введення («кава 85») і форматування грошей."""
from __future__ import annotations

import re

MAX_AMOUNT = 100_000_000

_NUM = r"\d[\d\s]*(?:[.,]\d{1,2})?"
_EXPR = rf"{_NUM}(?:\s*\+\s*{_NUM})*"
_AMOUNT_FIRST = re.compile(rf"^\s*({_EXPR})\s*(?:грн\.?|uah|₴)?\s+(.+?)\s*$", re.I)
_AMOUNT_LAST = re.compile(rf"^\s*(.*?[^\d\s.,+])\s+({_EXPR})\s*(?:грн\.?|uah|₴)?\s*$", re.I)


def _number(s: str) -> float | None:
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    if not s or s.count(".") > 1:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_amount(text: str | None) -> float | None:
    """'250', '1 250,50', '120+85+40', '99 грн' → число. Некоректне або ≤ 0 → None."""
    if not text:
        return None
    s = re.sub(r"(грн\.?|uah|₴)\s*$", "", text.strip(), flags=re.I).strip()
    if not s or not re.fullmatch(r"[\d\s.,+]+", s):
        return None
    total = 0.0
    for part in s.split("+"):
        n = _number(part)
        if n is None:
            return None
        total += n
    total = round(total, 2)
    return total if 0 < total <= MAX_AMOUNT else None


def parse_quick(text: str | None) -> tuple[float, str] | None:
    """Швидкий запис одним повідомленням: '85 кава', 'кава 85', 'АТБ 1 250,40', '120+60 таксі'."""
    if not text:
        return None
    for rx, a, c in ((_AMOUNT_FIRST, 1, 2), (_AMOUNT_LAST, 2, 1)):
        m = rx.match(text)
        if m:
            amount = parse_amount(m.group(a))
            comment = m.group(c).strip()
            if amount and comment:
                return amount, comment
    amount = parse_amount(text)
    return (amount, "") if amount else None


def fmt_money(value: float, currency: str = "грн") -> str:
    """1250 → '1 250 грн', 99.5 → '99,50 грн'."""
    value = round(float(value), 2)
    if value == int(value):
        s = f"{int(value):,}".replace(",", " ")
    else:
        s = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} {currency}".strip()
