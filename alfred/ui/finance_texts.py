"""Оформление финансов: суммы, категории, списки."""

import re
from typing import Optional

from ..brain.money import SYMBOL
from ..database.finance_repo import Operation
from .texts import MONTHS_GEN

CATEGORIES = [
    ("Продукты", "🛒", r"продукт|магазин|супермаркет|пятерочк|перекрест|магнит|лент[аеу]|еда домой|овощ|фрукт"),
    ("Рестораны", "🍽️", r"ресторан|кафе|ужин|обед|завтрак|бар\b|пицц|суши|бургер|доставк\w* еды|кофейн|кофе"),
    ("Такси", "🚕", r"такси|яндекс\s*го|убер|uber|ситимобил"),
    ("Транспорт", "🚇",
        r"метро|автобус|трамва|троллейбус|транспорт|бензин|заправк|парковк|каршеринг|электричк|проезд|поезд|"
        r"\bбск\b|подорожник|тройк\w*|транспортн\w*\s+карт|проездн"),
    ("Дом", "🏠", r"квартир|аренд|коммунал|жкх|свет\b|электричеств|ремонт|мебел|хозтовар"),
    ("Связь", "📱", r"телефон|связь|интернет|мобильн|сотов"),
    ("Здоровье", "💊", r"аптек|лекарств|врач|клиник|стоматолог|анализ|здоров"),
    ("Спорт", "🏋️", r"спорт|зал\b|фитнес|абонемент|тренировк|бассейн|инвентар|весл|гребл"),
    ("Одежда", "👕", r"одежд|обув|куртк|кроссовк|футболк|джинс"),
    ("Развлечения", "🎬", r"кино|театр|концерт|игр[аыу]|подписк|развлеч|клуб"),
    ("Подарки", "🎁", r"подар|цвет[ыо]"),
    ("Образование", "📚", r"книг|курс|учеб|образован|магистратур|универ"),
    ("Путешествия", "✈️", r"билет|самол[её]т|отел|гостиниц|путешеств|отпуск"),
    ("Зарплата", "💼", r"зарплат|аванс|оклад|премия|преми"),
]
EMOJI = {name: emoji for name, emoji, _ in CATEGORIES}


def match_category(*texts: Optional[str]) -> Optional[str]:
    for text in texts:
        if not text:
            continue
        low = text.casefold().replace("ё", "е")
        for name, _, pattern in CATEGORIES:
            if re.search(pattern, low):
                return name
    return None


def normalize_category(raw: Optional[str], *extra: Optional[str]) -> Optional[str]:
    known = match_category(raw, *extra)
    if known:
        return known
    if raw:
        raw = raw.strip()
        return raw[:1].upper() + raw[1:]
    return None


def emoji(category: Optional[str]) -> str:
    return EMOJI.get(category or "", "💸")


def money(value: float, currency: str = "RUB") -> str:
    neg = value < 0
    v = abs(round(value, 2))
    if v == int(v):
        body = f"{int(v):,}".replace(",", " ")
    else:
        body = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return f"{'−' if neg else ''}{body} {SYMBOL.get(currency, currency)}"


def op_line(o: Operation) -> str:
    day = f"{o.date.day} {MONTHS_GEN[o.date.month - 1][:3]}"
    sign = "−" if o.type == "expense" else "+"
    orig = f" ({money(o.original_amount, o.original_currency)})" if o.original_currency else ""
    what = o.category or o.description or ("Доход" if o.type == "income" else "Расход")
    return f"{emoji(o.category) if o.type == 'expense' else '💵'} {day} — {sign}{money(o.amount)}{orig} — {what}"
