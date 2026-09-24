"""Тексты раздела «Дни рождения»."""

from datetime import date

from ..database.birthday_repo import Birthday
from ..services.birthdays import next_date, turning
from .texts import MONTHS_GEN


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def years(n: int) -> str:
    return f"{n} {plural(n, 'год', 'года', 'лет')}"


def bday_date(b: Birthday) -> str:
    return f"{b.day} {MONTHS_GEN[b.month - 1]}" + (f" {b.year}" if b.year else "")


def when(b: Birthday, today: date) -> str:
    days = (next_date(b, today) - today).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == 2:
        return "послезавтра"
    return f"через {days} {plural(days, 'день', 'дня', 'дней')}"


def line(name: str, b: Birthday, today: date) -> str:
    """🎂 Мама — 12 марта · через 169 дней · исполнится 55 лет"""
    icon = "🥳" if next_date(b, today) == today else "🎂"
    age = turning(b, today)
    verb = "исполняется" if next_date(b, today) == today else "исполнится"
    tail = f" · {verb} {years(age)}" if age else ""
    return f"{icon} {name} — {b.day} {MONTHS_GEN[b.month - 1]} · {when(b, today)}{tail}"


def reminder_line(name: str, b: Birthday, today: date) -> str:
    age = turning(b, today)
    return f"{name}" + (f" — {years(age)}" if age else "")
