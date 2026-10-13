"""Точный разбор денег: суммы («2 500», «2,5 тысячи», «120к»), валюты и периоды («за неделю»)."""

import calendar
import re
from datetime import date, timedelta
from typing import Optional

from .dates import MONTH_ABBR, MONTHS, _month_num, _norm

_WORD_NUM = {"один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
             "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15, "двадцать": 20,
             "тридцать": 30, "сорок": 40, "пятьдесят": 50, "сто": 100, "двести": 200, "триста": 300,
             "пятьсот": 500}
_MULT = [(r"млн|миллион\w*|лям\w*", 1_000_000),
         (r"к|k|тыс\w*|тыщ\w*|тысяч\w*|тысяча|тысячу|тысячи|тр|т\.р\.?|косар\w*|косых", 1000)]

CURRENCIES = {
    "RUB": (r"руб\w*|₽|rub|р\.?(?=\s|$)", "₽"),
    "USD": (r"доллар\w*|бакс\w*|usd|\$", "$"),
    "EUR": (r"евро|эвро|еуро|евра|eur|€", "€"),
    "CNY": (r"юан\w*|cny|¥", "¥"),
    "GBP": (r"фунт\w*|gbp|£", "£"),
    "TRY": (r"лир\w*|try", "₺"),
    "KZT": (r"тенге|kzt|₸", "₸"),
    "BYN": (r"белорусск\w*\s+руб\w*|byn", "Br"),
    "AED": (r"дирхам\w*|aed", "AED"),
    "JPY": (r"иен\w*|йен\w*|jpy", "JPY"),
    "CHF": (r"франк\w*|chf", "CHF"),
    # Остальные валюты ЦБ. Где слово многозначное («лев», «крона», «бат») — только вместе со страной.
    "AMD": (r"драм\w*|армянск\w*|amd", "AMD"),
    "KRW": (r"вон(?:а|ы|ов)?\b|(?:южно)?корейск\w*|krw", "KRW"),
    "PLN": (r"злот\w*|польск\w*|pln", "PLN"),
    "THB": (r"бат(?:а|ы|ов)?\b|тайск\w*|thb", "THB"),
    "INR": (r"рупи\w*|индийск\w*|inr", "INR"),
    "IDR": (r"индонез\w*|idr", "IDR"),
    "GEL": (r"лари\b|грузинск\w*|gel", "GEL"),
    "KGS": (r"сом(?:а|ы|ов)?\b|киргизск\w*|кыргызск\w*|kgs", "KGS"),
    "TJS": (r"сомони|таджикск\w*|tjs", "TJS"),
    "UZS": (r"сум(?:а|ы|ов)?\b|узбекск\w*|uzs", "UZS"),
    "UAH": (r"гривн\w*|украинск\w*|uah", "UAH"),
    "AZN": (r"азербайджанск\w*|манат\w*|azn", "AZN"),
    "TMT": (r"туркменск\w*|tmt", "TMT"),
    "MDL": (r"молдавск\w*|mdl", "MDL"),
    "RON": (r"румынск\w*|ron", "RON"),
    "BGN": (r"болгарск\w*|bgn", "BGN"),
    "HUF": (r"форинт\w*|венгерск\w*|huf", "HUF"),
    "CZK": (r"чешск\w*|czk", "CZK"),
    "SEK": (r"шведск\w*|sek", "SEK"),
    "NOK": (r"норвежск\w*|nok", "NOK"),
    "DKK": (r"датск\w*|dkk", "DKK"),
    "HKD": (r"гонконгск\w*|hkd", "HKD"),
    "SGD": (r"сингапурск\w*|sgd", "SGD"),
    "CAD": (r"канадск\w*|cad", "CAD"),
    "AUD": (r"австралийск\w*|aud", "AUD"),
    "NZD": (r"новозеландск\w*|nzd", "NZD"),
    "BRL": (r"реал(?:а|ы|ов)?\b|бразильск\w*|brl", "BRL"),
    "ZAR": (r"рэнд\w*|ранд\w*|южноафриканск\w*|zar", "ZAR"),
    "EGP": (r"египетск\w*|egp", "EGP"),
    "QAR": (r"риал\w*|катарск\w*|qar", "QAR"),
    "VND": (r"донг\w*|вьетнамск\w*|vnd", "VND"),
    "RSD": (r"динар\w*|сербск\w*|rsd", "RSD"),
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
    """Сумма в тексте. Числа из дат и времени («24 сентября», «20 числа», «в 18», «10:00», «01.10»)
    и количества («2 пары», «3 кг») не считаются суммой. Если чисел несколько — сначала то, что явно сумма:
    с рублями/валютой/«тыс», после «за» («носки за 400»)."""
    if not text:
        return None
    t = _norm(text)
    if re.search(r"\bполтор\w+\s+тыс", t):
        return 1500.0
    corr = re.search(r"\bне\s+[\d\s.,]+\w*\s*,?\s*а\s+([\d][\d\s]*(?:[.,]\d+)?)\s*(\w+)?", t)
    if corr:  # «не 5000, а 3000»
        return _to_number(corr.group(1), corr.group(2))
    sure, plain = [], []
    for m in re.finditer(r"(?<![\d:.])(\d+(?:[  ]\d{3})*(?:[.,]\d+)?)(?:\s*(млн|миллион\w*|лям\w*|тысяч\w*|"
                         r"тыс\w*|тыщ\w*|косар\w*|косых|т\.р\.?|тр|к|k)(?![а-яa-z]))?(?![\d:])", t):
        after = t[m.end():m.end() + 14]
        word = after.strip().split()[0] if after.strip() else ""
        if not m.group(2) and _month_num(word) and any(word.startswith(s) for s in list(MONTHS) + list(MONTH_ABBR)):
            continue                                            # дата: «24 сентября», «15 окт»
        if re.match(r"[./]\d", after[:2]) or DATE_OR_QTY.match(after):
            continue                                            # «01.10», «20 числа», «2 пары», «3 кг»
        value = _to_number(m.group(1), m.group(2))
        if value is None:
            continue
        before = t[max(0, m.start() - 12):m.start()]
        if m.group(2) or CURRENCY_AFTER.match(after) or re.search(r"\b(?:за|на\s+сумму|стоимостью|стоил\w*)\s*$", before):
            sure.append(value)
        elif not re.search(r"\b(?:в|к|с|до)\s*$", before):     # «в 18», «к 9» — это время
            plain.append(value)
    if sure or plain:
        return (sure or plain)[0]
    m = re.search(r"\b(" + "|".join(sorted(_WORD_NUM, key=len, reverse=True)) + r")\s+(тысяч\w*|тысяч[аи]|тысячу)",
                  t)
    if m:
        return float(_WORD_NUM[m.group(1)] * 1000)
    if re.search(r"\bтысяч[ау]\b", t):
        return 1000.0
    m = re.search(r"\b(сто|двести|триста|четыреста|пятьсот|шестьсот|семьсот|восемьсот|девятьсот)\b", t)
    if m:                                                       # «пятьсот», «сто рублей»
        return float(HUNDREDS[m.group(1)])
    return None


HUNDREDS = {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600,
            "семьсот": 700, "восемьсот": 800, "девятьсот": 900}


DATE_OR_QTY = re.compile(r"\s*(?:-?\s*(?:го|е|му|ое|ого)\b\s*)?(?:числ|раз\b|раза\b|пар[аыу]?\b|шт|штук|кг|кило|"
                         r"гр?\b|грамм|л\b|литр|мл\b|человек|чел\b|дн[еяй]|день|недел|месяц|час|мин|сек|км\b|"
                         r"этаж|поездк|бутыл|пачк|кус|порци|бокал|чашк|упаков|коробк|банк[аи]\b|ящик|место|места)")
CURRENCY_AFTER = re.compile(r"\s*(?:р\b|р\.|руб|₽|\$|€|евро|доллар|бакс|юан|тенге|фунт|лир|usd|eur|rub)")


def _to_number(raw: str, mult: Optional[str]) -> Optional[float]:
    digits = raw.strip().replace(" ", "").replace(" ", "").replace(",", ".")
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
        return _currency_by_typo(t)
    codes = {c for _, c in found}
    if len(codes) > 1 and "BYN" in codes:
        return "BYN"
    # «Индонезийская рупия», «египетский фунт», «канадский доллар»: страна важнее общего слова
    for specific, general in (("IDR", "INR"), ("EGP", "GBP"), ("CAD", "USD"), ("AUD", "USD"), ("NZD", "USD"),
                              ("HKD", "USD"), ("SGD", "USD"), ("TJS", "KGS")):
        if specific in codes and general in codes:
            return specific
    return sorted(found)[0][1]


# Опечатки: «долар», «тенгэ», «рублй». Проверяем, только если точного совпадения нет.
_TYPO_WORDS = {"доллар": "USD", "доллара": "USD", "долларов": "USD", "долларах": "USD", "тенге": "KZT",
               "рубль": "RUB", "рубля": "RUB", "рублей": "RUB", "рублях": "RUB", "юаней": "CNY", "юаня": "CNY",
               "фунтов": "GBP", "фунта": "GBP", "дирхам": "AED", "дирхамов": "AED", "франков": "CHF",
               "франка": "CHF", "бакса": "USD", "баксов": "USD"}


def _currency_by_typo(t: str) -> Optional[str]:
    from ..services.search import typo_close
    for word in re.findall(r"[а-я]+", t):
        for known, code in _TYPO_WORDS.items():
            if typo_close(word, known):
                return code
    return None


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
    if re.search(r"\bпозавчера\b", t):
        y = today - timedelta(days=2)
        return y, y, "за позавчера"
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
    if re.search(r"(?<![а-яё])недел", t):                # «неделя», но не «поНЕДЕЛьник»
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
