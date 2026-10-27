"""SQLite: операції, оплачені регулярні платежі, надіслані нагадування.

Кожна операція має стан синхронізації з Google Sheets (add / update / delete / ok),
тому якщо Google недоступний, нічого не губиться — бот дошле зміни пізніше.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
    amount      REAL NOT NULL CHECK (amount > 0),
    category    TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    user_id     INTEGER NOT NULL,
    user_name   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    sync        TEXT NOT NULL DEFAULT 'add'
);
CREATE INDEX IF NOT EXISTS ix_operations_created ON operations (created_at);
CREATE INDEX IF NOT EXISTS ix_operations_sync ON operations (sync);

CREATE TABLE IF NOT EXISTS recurring_paid (
    key     TEXT NOT NULL,
    period  TEXT NOT NULL,          -- 'YYYY-MM'
    op_id   INTEGER NOT NULL,
    PRIMARY KEY (key, period)
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    key  TEXT NOT NULL,
    day  TEXT NOT NULL,             -- 'YYYY-MM-DD'
    PRIMARY KEY (key, day)
);
"""

TS = "%Y-%m-%d %H:%M:%S"
EDITABLE = {"amount", "category", "comment", "kind"}


@dataclass
class Operation:
    id: int
    kind: str
    amount: float
    category: str
    comment: str
    user_id: int
    user_name: str
    created_at: datetime
    deleted: bool = False
    sync: str = "add"

    @property
    def period(self) -> str:
        return self.created_at.strftime("%Y-%m")

    @property
    def signed(self) -> float:
        return self.amount if self.kind == "income" else -self.amount


def _op(row: sqlite3.Row) -> Operation:
    d = dict(row)
    d["created_at"] = datetime.strptime(d["created_at"], TS)
    d["deleted"] = bool(d["deleted"])
    return Operation(**d)


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- операції ----------
    def add(self, kind: str, amount: float, category: str, comment: str,
            user_id: int, user_name: str, created_at: datetime) -> Operation:
        cur = self.conn.execute(
            "INSERT INTO operations (kind, amount, category, comment, user_id, user_name, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, round(amount, 2), category, comment.strip(), user_id, user_name, created_at.strftime(TS)))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, op_id: int) -> Operation | None:
        row = self.conn.execute("SELECT * FROM operations WHERE id = ?", (op_id,)).fetchone()
        return _op(row) if row else None

    def update(self, op_id: int, **fields) -> Operation | None:
        bad = set(fields) - EDITABLE
        if bad:
            raise ValueError(f"не можна змінювати: {bad}")
        op = self.get(op_id)
        if op is None or op.deleted:
            return None
        if "amount" in fields:
            fields["amount"] = round(fields["amount"], 2)
        # якщо рядок ще не потрапив у таблицю, він так і залишиться «add»
        sync = "add" if op.sync == "add" else "update"
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE operations SET {sets}, sync = ? WHERE id = ?", (*fields.values(), sync, op_id))
        self.conn.commit()
        return self.get(op_id)

    def delete(self, op_id: int) -> Operation | None:
        """М'яке видалення: рядок лишається в базі, з таблиці його прибере синхронізація."""
        op = self.get(op_id)
        if op is None or op.deleted:
            return None
        sync = "ok" if op.sync == "add" else "delete"  # у таблиці його ще не було — прибирати нічого
        self.conn.execute("UPDATE operations SET deleted = 1, sync = ? WHERE id = ?", (sync, op_id))
        self.conn.execute("DELETE FROM recurring_paid WHERE op_id = ?", (op_id,))  # платіж знову «не оплачений»
        self.conn.commit()
        return self.get(op_id)

    def last(self, limit: int = 10) -> list[Operation]:
        rows = self.conn.execute(
            "SELECT * FROM operations WHERE deleted = 0 ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [_op(r) for r in rows]

    def period(self, year: int, month: int) -> list[Operation]:
        rows = self.conn.execute(
            "SELECT * FROM operations WHERE deleted = 0 AND substr(created_at, 1, 7) = ? ORDER BY created_at",
            (f"{year:04d}-{month:02d}",))
        return [_op(r) for r in rows]

    def since(self, start: datetime) -> list[Operation]:
        rows = self.conn.execute(
            "SELECT * FROM operations WHERE deleted = 0 AND created_at >= ? ORDER BY created_at",
            (start.strftime(TS),))
        return [_op(r) for r in rows]

    # ---------- синхронізація ----------
    def pending(self) -> list[Operation]:
        return [_op(r) for r in self.conn.execute("SELECT * FROM operations WHERE sync != 'ok' ORDER BY id")]

    def mark_synced(self, ids: list[int]) -> None:
        self.conn.executemany("UPDATE operations SET sync = 'ok' WHERE id = ?", [(i,) for i in ids])
        self.conn.commit()

    # ---------- регулярні платежі ----------
    def paid_op(self, key: str, period: str) -> Operation | None:
        row = self.conn.execute("SELECT op_id FROM recurring_paid WHERE key = ? AND period = ?",
                                (key, period)).fetchone()
        return self.get(row["op_id"]) if row else None

    def is_paid(self, key: str, period: str) -> bool:
        return self.paid_op(key, period) is not None

    def mark_paid(self, key: str, period: str, op_id: int) -> bool:
        """False, якщо хтось уже позначив оплату (двоє натиснули кнопку одночасно)."""
        try:
            self.conn.execute("INSERT INTO recurring_paid (key, period, op_id) VALUES (?, ?, ?)", (key, period, op_id))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def reminder_sent(self, key: str, day: str) -> bool:
        return self.conn.execute("SELECT 1 FROM reminders_sent WHERE key = ? AND day = ?",
                                 (key, day)).fetchone() is not None

    def mark_reminder(self, key: str, day: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO reminders_sent (key, day) VALUES (?, ?)", (key, day))
        self.conn.commit()
