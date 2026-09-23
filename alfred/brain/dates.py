"""Точный разбор дат на русском языке. Даты считает код, а не ИИ.

Понимает: сегодня, завтра, послезавтра, в/на пятницу, в следующую пятницу,
на выходных, через 3 дня, через неделю, через 2 недели, через месяц,
15 октября, 15-го октября, 01.10, 01.10.2026.
"""

import calendar
import re
from datetime import date, timedelta
from typing import Optional

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_MONTH_RE = r"(январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|августа?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])"

WEEKDAYS = [("понедельн", 0), ("вторник", 1), ("сред", 2), ("четверг", 3),
            ("пятниц", 4), ("суббот", 5), ("воскресен", 6)]

NUMBERS = {"один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
           "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пару": 2}


def _norm(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _month_num(word: str) -> int:
    for stem, num in MONTHS.items():
        if word.startswith(stem) and not (stem == "ма" and word.startswith("март")):
            return num
    return 0


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _future(y_candidate: date, today: date) -> date:
    """Дата без года: если в этом году уже прошла — значит, следующий год."""
    return y_candidate if y_candidate >= today else y_candidate.replace(year=y_candidate.year + 1)


def find_dates(text: str, today: date) -> list[date]:
    """Все даты, которые удалось найти в тексте, в порядке появления."""
    t = _norm(text)
    found: list[tuple[int, date]] = []

    def add(pos: int, d: Optional[date]):
        if d and all(p != pos for p, _ in found):
            found.append((pos, d))

    for m in re.finditer(r"\bпослезавтра\b", t):
        add(m.start(), today + timedelta(days=2))
    for m in re.finditer(r"(?<!после)\bзавтра\b", t):
        add(m.start(), today + timedelta(days=1))
    for m in re.finditer(r"\bсегодня\b", t):
        add(m.start(), today)

    # через N дней / недель / месяцев
    num = r"(\d+|" + "|".join(NUMBERS) + r")?"
    for m in re.finditer(r"через\s+" + num + r"\s*(дн|день|недел|месяц)", t):
        raw, unit = m.group(1), m.group(2)
        n = 1 if not raw else (int(raw) if raw.isdigit() else NUMBERS[raw])
        if unit in ("дн", "день"):
            add(m.start(), today + timedelta(days=n))
        elif unit == "недел":
            add(m.start(), today + timedelta(weeks=n))
        else:
            add(m.start(), _add_months(today, n))

    # 15 октября, 15-го октября, 15 октября 2027
    for m in re.finditer(r"\b(\d{1,2})(?:-?го)?\s+" + _MONTH_RE + r"(?:\s+(\d{4}))?", t):
        day, month = int(m.group(1)), _month_num(m.group(2))
        try:
            d = date(int(m.group(3)) if m.group(3) else today.year, month, day)
        except ValueError:
            continue
        add(m.start(), d if m.group(3) else _future(d, today))

    # 01.10, 01.10.2026, 1/10
    for m in re.finditer(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", t):
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            y = today.year if not year else (int(year) + 2000 if len(year) == 2 else int(year))
            d = date(y, month, day)
        except ValueError:
            continue
        add(m.start(), d if year else _future(d, today))

    # (в/на) (следующую) пятницу
    for m in re.finditer(r"(следующ\w*\s+)?(понедельн\w*|вторник\w*|сред[уаы]\w*|четверг\w*|"
                         r"пятниц\w*|суббот\w*|воскресен\w*)", t):
        wd = next(n for stem, n in WEEKDAYS if m.group(2).startswith(stem))
        if m.group(1):
            next_monday = today + timedelta(days=7 - today.weekday())
            d = next_monday + timedelta(days=wd)
        else:
            ahead = (wd - today.weekday()) % 7 or 7
            d = today + timedelta(days=ahead)
        add(m.start(), d)

    for m in re.finditer(r"\bна\s+выходн", t):
        ahead = (5 - today.weekday()) % 7
        add(m.start(), today + timedelta(days=ahead))

    return [d for _, d in sorted(found, key=lambda x: x[0])]


def parse_date(phrase: Optional[str], today: date) -> Optional[date]:
    """Дата из короткой фразы («в пятницу»). None, если не найдена или их несколько."""
    if not phrase:
        return None
    dates = find_dates(phrase, today)
    return dates[-1] if dates else None
