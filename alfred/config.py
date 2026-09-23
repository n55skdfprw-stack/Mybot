"""Настройки Альфреда. Все секреты берутся только из переменных окружения."""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    telegram_token: str
    owner_id: int
    gigachat_key: str
    gigachat_model: str
    gigachat_model_pro: str
    database_path: str
    timezone: ZoneInfo
    default_city: str


def _default_db_path() -> str:
    # Если на хостинге подключено постоянное хранилище в /data — используем его.
    if os.path.isdir("/data"):
        return "/data/alfred.db"
    return "alfred.db"


def load_config() -> Config:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    owner = os.getenv("OWNER_ID", "").strip()
    giga = os.getenv("GIGACHAT_AUTH_KEY", "").strip()

    missing = [name for name, value in
               (("TELEGRAM_TOKEN", token), ("OWNER_ID", owner), ("GIGACHAT_AUTH_KEY", giga))
               if not value]
    if missing:
        raise RuntimeError("Не заданы переменные окружения: " + ", ".join(missing))
    if not owner.isdigit():
        raise RuntimeError("OWNER_ID должен состоять только из цифр")

    return Config(
        telegram_token=token,
        owner_id=int(owner),
        gigachat_key=giga,
        gigachat_model=os.getenv("GIGACHAT_MODEL", "GigaChat-2").strip(),
        gigachat_model_pro=os.getenv("GIGACHAT_MODEL_PRO", "GigaChat-2-Pro").strip(),
        database_path=os.getenv("DATABASE_PATH", "").strip() or _default_db_path(),
        timezone=ZoneInfo(os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow").strip()),
        default_city=os.getenv("DEFAULT_CITY", "Санкт-Петербург").strip(),
    )
