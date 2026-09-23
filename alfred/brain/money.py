"""Точный разбор денег: суммы («2 500», «2,5 тысячи», «120к»), валюты и периоды («за неделю»)."""

import calendar
import re
from datetime import date, timedelta
from typing import Optional

from .dates import MONTHS, _month_num, _norm

_WORD_NUM = {"один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
             "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15, "двадцать": 20,
             "тридцать": 30, "сорок": 40, "пятьдесят": 50, "сто": 100, "двести": 200, "триста": 300,
             "пятьсот": 500}
_MULT = [(r"млн|миллион\w*|лям\w*", 1_000_000),
         (r"к|k|тыс\w*|тыщ\w*|тысяч\w*|тысяча|тысячу|тысячи|тр|т\.р\.?|косар\w*|косых", 1000)]

CURRENCIES = {
    "RUB": (r"руб\w*|₽|rub|р\.?(?=\s|$)", "₽"),
    "USD": (r"доллар\w*|бакс\w*|usd|\$", "$"),
    "EUR": (r"евро|eur|€", "€"),
    "CNY": (r"юан\w*|cny|¥", "¥"),
    "GBP": (r"фунт\w*|gbp|£", "£"),
    "TRY": (r"лир\w*|try", "₺"),
    "KZT": (r"тенге|kzt|₸", "₸"),
    "BYN": (r"белорусск\w*\s+руб\w*|byn", "Br"),
    "AED": (r"дирхам\w*|aed", "AED"),
    "JPY": (r"иен\w*|йен\w*|jpy", "JPY"),
    "CHF": (r"франк\w*|chf", "CHF"),
}
SYMBOL = {code: sym for code, (_, sym) in CURRENCIES.items()}


def _mult(word: Optional[str]) -> int:
    if not word:
        return 1
    for pattern, m in _MULT:
        if re.fullmatch(pattern, word):
            return m
    return 1


def parse_amount(text: Optional[str]) -> Optional[float]:
    """Первая сумма в тексте. Числа из дат («24 сентября», «10:00», «01.10») пропускаются."""
    if not text:
        return None
    t = _norm(text)
    if re.search(r"\bполтор\w+\s+тыс", t):
        return 1500.0
    corr = re.search(r"\bне\s+[\d\s.,]+\w*\s*,?\s*а\s+([\d][\d\s]*(?:[.,]\d+)?)\s*(\w+)?", t)
    if corr:  # «не 5000, а 3000»
        return _to_number(corr.group(1), corr.group(2))
    for m in re.finditer(r"(?<![\d:.])(\d[\d\s]*(?:[.,]\d+)?)(?:\s*(млн|миллион\w*|лям\w*|тысяч\w*|тыс\w*|тыщ\w*|"
                         r"косар\w*|косых|т\.р\.?|тр|к|k)(?![а-яa-z]))?(?![\d:])", t):
        after = t[m.end():m.end() + 12].strip()
        if not m.group(2) and any(after.startswith(stem) for stem in MONTHS) and _month_num(after.split()[0] if after else ""):
            continue  # это дата: «24 сентября»
        if re.match(r"[./]\d", t[m.end():m.end() + 2]):
            continue
        value = _to_number(m.group(1), m.group(2))
        if value is not None:
            return value
    m = re.search(r"\b(" + "|".join(sorted(_WORD_NUM, key=len, reverse=True)) + r")\s+(тысяч\w*|тысяч[аи]|тысячу)",
                  t)
    if m:
        return float(_WORD_NUM[m.group(1)] * 1000)
    if re.search(r"\bтысяч[ау]\b", t):
        return 1000.0
    return None


def _to_number(raw: str, mult: Optional[str]) -> Optional[float]:
    digits = raw.strip().replace(" ", "").replace(",", ".")
    try:
        value = float(digits)
    except ValueError:
        return None
    return round(value * _mult(mult), 2)


def parse_currency(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = _norm(text)
    found = []
    for code, (pattern, _) in CURRENCIES.items():
        m = re.search(r"(?<![а-яa-z])(?:" + pattern + r")", t)
        if m:
            found.append((m.start(), code))
    if not found:
        return None
    if len({c for _, c in found}) > 1 and "BYN" in {c for _, c in found}:
        return "BYN"
    return sorted(found)[0][1]


def parse_conversion(text: str) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """«5000 рублей в евро» → (5000, RUB, EUR); «сколько 100 долларов» → (100, USD, None)."""
    t = _norm(text)
    amount = parse_amount(t)
    parts = re.split(r"\s(?:в|во|на)\s(?=[^\d]*$)", t, maxsplit=1)
    src = parse_currency(parts[0]) if parts else None
    dst = parse_currency(parts[1]) if len(parts) > 1 else None
    if src and dst == src:
        dst = None
    return amount, src, dst


# ------------------------------------------------------------ периоды

VAGUE = "VAGUE"


def parse_period(text: Optional[str], today: date) -> Optional[tuple[date, date, str]] | str:
    """(начало, конец, подпись) или VAGUE для «за последнее время», или None — период не назван."""
    if not text:
        return None
    t = _norm(text)
    if re.search(r"последнее\s+время|недавно|в\s+последнее", t):
        return VAGUE
    if re.search(r"\bсегодня\b", t):
        return today, today, "за сегодня"
    if re.search(r"\bвчера\b", t):
        y = today - timedelta(days=1)
        return y, y, "за вчера"
    m = re.search(r"последни[ех]\s+(\d+)\s+дн", t)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n - 1), today, f"за последние {n} дн."
    if re.search(r"прошл\w+\s+недел", t):
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6), "за прошлую неделю"
    if re.search(r"недел", t):
        return today - timedelta(days=today.weekday()), today, "за неделю"
    if re.search(r"прошл\w+\s+месяц", t):
        last_prev = today.replace(day=1) - timedelta(days=1)
        return last_prev.replace(day=1), last_prev, "за прошлый месяц"
    if re.search(r"месяц", t):
        return today.replace(day=1), today, "за месяц"
    if re.search(r"\bгод\b|\bгода\b|\bгоду\b", t):
        return today.replace(month=1, day=1), today, "за год"
    for word in t.split():
        mo = _month_num(word)
        if mo and (word.startswith(("январ", "феврал", "март", "апрел", "мая", "май", "июн", "июл", "август",
                                    "сентябр", "октябр", "ноябр", "декабр"))):
            year = today.year if mo <= today.month else today.year - 1
            last = calendar.monthrange(year, mo)[1]
            names = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
                     "октябрь", "ноябрь", "декабрь"]
            return date(year, mo, 1), min(date(year, mo, last), today), f"за {names[mo - 1]}"
    return None
