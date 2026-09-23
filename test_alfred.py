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


# ============================================================ время

_HOUR_WORDS = {"час": 1, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
               "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
               "двенадцать": 12}
_HALF = {"первого": 0, "второго": 1, "третьего": 2, "четвертого": 3, "пятого": 4, "шестого": 5,
         "седьмого": 6, "восьмого": 7, "девятого": 8, "десятого": 9, "одиннадцатого": 10,
         "двенадцатого": 11}
_H = r"(\d{1,2}|" + "|".join(sorted(_HOUR_WORDS, key=len, reverse=True)) + r")"
_PART = r"(?:\s*(?:час(?:а|ов)?))?(?:\s+(утра|дня|вечера|ночи))?"


def _hour_value(raw: str) -> int:
    return int(raw) if raw.isdigit() else _HOUR_WORDS[raw]


def _apply_daypart(h: int, part: str | None, explicit_zero: bool) -> int:
    """«в 6» без уточнения — скорее вечер (18:00); «в 10» — утро; «в 6 утра» — 06:00."""
    if part == "утра":
        return 0 if h == 12 else h
    if part in ("дня", "вечера"):
        return h + 12 if h < 12 else h
    if part == "ночи":
        return h if h <= 5 else (0 if h == 12 else h + 12)
    if explicit_zero:
        return h
    return h + 12 if 1 <= h <= 6 else h


def _fmt(h: int, m: int) -> str | None:
    if 0 <= h <= 23 and 0 <= m <= 59:
        return f"{h:02d}:{m:02d}"
    return None


def _one_time(t: str) -> str | None:
    t = t.strip()
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?:\s+(утра|дня|вечера|ночи))?", t)
    if m:
        h = _apply_daypart(int(m.group(1)), m.group(3), m.group(1).startswith("0") or int(m.group(1)) >= 7)
        return _fmt(h, int(m.group(2)))
    m = re.fullmatch(r"пол\s*-?\s*(\w+?)(?:\s+(утра|дня|вечера|ночи))?", t)
    if m and m.group(1) in _HALF:
        h = _apply_daypart(_HALF[m.group(1)] or 12, m.group(2), False) % 24
        return _fmt(h, 30)
    if t == "полдень":
        return "12:00"
    if t == "полночь":
        return "00:00"
    m = re.fullmatch(_H + _PART, t)
    if m:
        return _fmt(_apply_daypart(_hour_value(m.group(1)), m.group(2), m.group(1).startswith("0")), 0)
    return None


def parse_time_range(text: str | None) -> tuple[str | None, str | None]:
    """«в 18:00» → ("18:00", None); «с 10 до 12» → ("10:00", "12:00"); «в полседьмого» → ("18:30", None)."""
    if not text:
        return None, None
    t = _norm(text)
    token = r"(\d{1,2}[:.]\d{2}(?:\s+(?:утра|дня|вечера|ночи))?|пол\s*-?\s*\w+(?:\s+(?:утра|дня|вечера|ночи))?|" \
            + _H + _PART.replace("(утра|дня|вечера|ночи)", "(?:утра|дня|вечера|ночи)") + r")"
    m = re.search(r"\bс\s+" + token + r"\s+до\s+" + token, t)
    if m:
        parts = re.split(r"\s+до\s+", m.group(0)[2:].strip(), maxsplit=1)
        a, b = _one_time(parts[0]), _one_time(parts[1])
        if a and b and b <= a and int(b[:2]) < 12:  # «с 10 до 2» → до 14:00
            b = _fmt(int(b[:2]) + 12, int(b[3:]))
        return a, b
    m = re.search(r"(\d{1,2}[:.]\d{2})\s*[-–—]\s*(\d{1,2}[:.]\d{2})", t)
    if m:
        return _one_time(m.group(1)), _one_time(m.group(2))
    for m in re.finditer(r"\b(?:в|на|к)\s+" + token, t):
        value = _one_time(m.group(1))
        if value:
            return value, None
    m = re.search(r"(\d{1,2}[:.]\d{2}(?:\s+(?:утра|дня|вечера|ночи))?)", t)
    if m:
        return _one_time(m.group(1)), None
    m = re.search(r"\b(пол\s*-?\s*\w+|полдень|полночь)\b", t)
    if m:
        return _one_time(m.group(1)), None
    m = re.fullmatch(_H + _PART, t.strip())
    if m:
        return _one_time(m.group(0)), None
    return None, None


# ============================================================ повторения

def parse_repeat(text: str | None, start: date) -> tuple[str | None, list[int]]:
    """Возвращает (вид, дни недели): ("weekly", [0, 3]), ("monthly", []) или (None, [])."""
    if not text:
        return None, []
    t = _norm(text)
    if re.search(r"будн", t):
        return "weekly", [0, 1, 2, 3, 4]
    if re.search(r"выходн", t):
        return "weekly", [5, 6]
    if re.search(r"каждый\s+день|ежедневн|все\s+дни", t):
        return "weekly", [0, 1, 2, 3, 4, 5, 6]
    if re.search(r"месяц|ежемесячн", t):
        return "monthly", []
    days = sorted({n for stem, n in WEEKDAYS if re.search(stem, t)})
    if days:
        return "weekly", days
    if re.search(r"недел|еженедельн", t):
        return "weekly", [start.weekday()]
    return None, []


MONTHS_NOM_GEN = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7,
                  "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def parse_until(text: str | None, today: date) -> date | None:
    """«до конца октября», «до 15 ноября», «на ближайшие две недели», «на месяц»."""
    if not text:
        return None
    t = _norm(text)
    m = re.search(r"до\s+конца\s+(\w+)", t)
    if m:
        word = m.group(1)
        if word.startswith("месяц"):
            y, mo = today.year, today.month
        elif word.startswith("недел"):
            return today + timedelta(days=6 - today.weekday())
        elif word.startswith("год"):
            return date(today.year, 12, 31)
        else:
            mo = _month_num(word)
            if not mo:
                return None
            y = today.year if mo >= today.month else today.year + 1
        return date(y, mo, calendar.monthrange(y, mo)[1])
    num = r"(\d+|" + "|".join(NUMBERS) + r")?"
    m = re.search(r"(?:на|ближайшие|следующие)\s+(?:ближайшие\s+|следующие\s+)?" + num + r"\s*(недел|месяц|дн)", t)
    if m:
        raw = m.group(1)
        n = 1 if not raw else (int(raw) if raw.isdigit() else NUMBERS[raw])
        if m.group(2) == "недел":
            return today + timedelta(weeks=n) - timedelta(days=1)
        if m.group(2) == "дн":
            return today + timedelta(days=n - 1)
        return _add_months(today, n) - timedelta(days=1)
    dates = find_dates(t, today)
    return dates[-1] if dates else None
