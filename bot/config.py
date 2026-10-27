"""Налаштування з .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_users(raw: str) -> dict[int, str]:
    """'111:Ярослав, 222:Оля, 333' → {111: 'Ярослав', 222: 'Оля', 333: ''}"""
    users: dict[int, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        uid, _, name = part.partition(":")
        users[int(uid.strip())] = name.strip()
    return users


@dataclass
class Config:
    bot_token: str
    users: dict[int, str] = field(default_factory=dict)
    timezone: str = "Europe/Kyiv"
    currency: str = "грн"
    db_path: Path = Path("data/budget.db")
    settings_file: Path = Path("settings.json")
    sheet_id: str = ""
    google_credentials: Path = Path("data/credentials.json")
    remind_hour: int = 10
    remind_days_before: int = 2
    sync_interval: int = 20

    def now(self) -> datetime:
        """Поточний локальний час без tzinfo (так зберігаємо в базі)."""
        return datetime.now(ZoneInfo(self.timezone)).replace(tzinfo=None, microsecond=0)

    def name_of(self, user_id: int, fallback: str = "") -> str:
        return self.users.get(user_id) or fallback or str(user_id)


def load_config() -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # dotenv не обов'язковий, якщо змінні задані в оточенні (Docker)
        pass
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не заданий. Скопіюйте .env.example у .env і заповніть.")
    users = parse_users(os.getenv("ALLOWED_USERS", ""))
    if not users:
        raise SystemExit("ALLOWED_USERS порожній — вкажіть Telegram ID членів сім'ї.")
    return Config(
        bot_token=token,
        users=users,
        timezone=os.getenv("TIMEZONE", "Europe/Kyiv"),
        currency=os.getenv("CURRENCY", "грн"),
        db_path=Path(os.getenv("DB_PATH", "data/budget.db")),
        settings_file=Path(os.getenv("SETTINGS_FILE", "settings.json")),
        sheet_id=os.getenv("SHEET_ID", "").strip(),
        google_credentials=Path(os.getenv("GOOGLE_CREDENTIALS", "data/credentials.json")),
        remind_hour=int(os.getenv("REMIND_HOUR", "10")),
        remind_days_before=int(os.getenv("REMIND_DAYS_BEFORE", "2")),
        sync_interval=int(os.getenv("SYNC_INTERVAL", "20")),
    )
