"""Категорії та регулярні платежі. Редагуються без коду: у Google Sheets або в settings.json."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

EXPENSE, INCOME = "expense", "income"
KIND_LABEL = {EXPENSE: "Витрата", INCOME: "Дохід"}
_KIND_FROM_TEXT = {"витрата": EXPENSE, "витрати": EXPENSE, "expense": EXPENSE,
                   "дохід": INCOME, "доходи": INCOME, "income": INCOME}
_FALSE = {"ні", "нет", "no", "false", "0", "-", "хибність"}

CATEGORY_HEADER = ["Тип", "Категорія", "Емодзі", "Ліміт на місяць", "Ключові слова"]
RECURRING_HEADER = ["Назва", "Сума", "День місяця", "Категорія", "Активний"]


@dataclass(frozen=True)
class Category:
    kind: str
    name: str
    emoji: str = ""
    limit: float | None = None
    keywords: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}".strip()


@dataclass(frozen=True)
class Recurring:
    name: str
    amount: float
    day: int
    category: str
    active: bool = True

    @property
    def key(self) -> str:
        """Короткий стабільний ключ для callback_data (обмеження Telegram — 64 байти)."""
        return hashlib.sha1(self.name.lower().encode()).hexdigest()[:10]


@dataclass
class Settings:
    categories: list[Category] = field(default_factory=list)
    recurring: list[Recurring] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[Category]:
        return [c for c in self.categories if c.kind == kind]

    def get(self, kind: str, name: str) -> Category | None:
        return next((c for c in self.categories if c.kind == kind and c.name.lower() == name.lower()), None)

    def label(self, kind: str, name: str) -> str:
        cat = self.get(kind, name)
        return cat.label if cat else f"📌 {name}"

    def find_by_keyword(self, text: str, kind: str = EXPENSE) -> Category | None:
        """'кава в обід' → Кафе (якщо 'кава' є в ключових словах). Слово має починатися з ключового."""
        words = re.findall(r"[\w'ʼ’]+", text.lower())
        for cat in self.by_kind(kind):
            for kw in cat.keywords:
                if any(w.startswith(kw) for w in words) or cat.name.lower() in words:
                    return cat
        return None

    def recurring_by_key(self, key: str) -> Recurring | None:
        return next((r for r in self.recurring if r.key == key), None)


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _keywords(value) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,;\n]", str(value or ""))
    return tuple(k.strip().lower() for k in items if k and k.strip())


def settings_from_json(data: dict) -> Settings:
    cats = [Category(kind=c.get("kind", EXPENSE), name=c["name"], emoji=c.get("emoji", ""),
                     limit=_num(c.get("limit")), keywords=_keywords(c.get("keywords")))
            for c in data.get("categories", [])]
    recs = [Recurring(name=r["name"], amount=float(r["amount"]), day=int(r["day"]),
                      category=r.get("category", "Інше"), active=r.get("active", True))
            for r in data.get("recurring", [])]
    return Settings(cats, recs)


def load_settings_file(path: Path) -> Settings:
    if not path.exists():
        example = path.with_name("settings.example.json")
        path = example if example.exists() else path
    return settings_from_json(json.loads(path.read_text(encoding="utf-8")))


def settings_from_rows(category_rows: list[list], recurring_rows: list[list]) -> Settings:
    """Рядки з аркушів «Категорії» та «Регулярні платежі» (перший рядок — шапка). Криві рядки пропускаємо."""
    cats: list[Category] = []
    for row in category_rows[1:]:
        row = list(row) + [""] * 5
        kind = _KIND_FROM_TEXT.get(str(row[0]).strip().lower())
        name = str(row[1]).strip()
        if not kind or not name:
            continue
        cats.append(Category(kind, name, str(row[2]).strip(), _num(row[3]), _keywords(row[4])))
    recs: list[Recurring] = []
    for row in recurring_rows[1:]:
        row = list(row) + [""] * 5
        name, amount, day = str(row[0]).strip(), _num(row[1]), _num(row[2])
        if not name or not amount or not day or not 1 <= int(day) <= 31:
            continue
        active = str(row[4]).strip().lower() not in _FALSE
        recs.append(Recurring(name, amount, int(day), str(row[3]).strip() or "Інше", active))
    return Settings(cats, recs)


def settings_to_rows(s: Settings) -> tuple[list[list], list[list]]:
    """Для першого заповнення аркушів налаштувань."""
    cats = [CATEGORY_HEADER] + [[KIND_LABEL[c.kind], c.name, c.emoji, c.limit or "", ", ".join(c.keywords)]
                                for c in s.categories]
    recs = [RECURRING_HEADER] + [[r.name, r.amount, r.day, r.category, "так" if r.active else "ні"]
                                 for r in s.recurring]
    return cats, recs
