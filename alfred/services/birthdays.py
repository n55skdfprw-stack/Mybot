"""Дни рождения: разбор даты из слов, ближайшая дата, возраст, проверка записи."""

import calendar
import re
from datetime import date
from typing import Optional

from ..brain.dates import find_dates
from ..database.birthday_repo import Birthday, BirthdayRepository
from .tasks import VerificationError

_MONTHS = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("мая", 5), ("май", 5), ("июн", 6), ("июл", 7),
           ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12)]


def _year(raw: Optional[str], today: date) -> Optional[int]:
    if not raw:
        return None
    y = int(raw)
    if y < 100:  # «98» → 1998, «05» → 2005
        y += 2000 if y <= today.year % 100 else 1900
    return y if 1900 <= y <= today.year else None


def _valid(day: int, month: int, year: Optional[int]) -> bool:
    if not 1 <= month <= 12:
        return False
    last = 29 if month == 2 and year is None else calendar.monthrange(year or 2000, month)[1]
    return 1 <= day <= last


def parse_birthday(text: Optional[str], today: date) -> Optional[tuple[int, int, Optional[int]]]:
    """«12 марта», «5 мая 1998», «12.03», «05.05.98» → (день, месяц, год или None).

    Если в тексте несколько дат («не 12, а 14 марта») — берём последнюю.
    """
    if not text:
        return None
    t = text.lower().replace("ё", "е")
    found = []
    for m in re.finditer(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?\b", t):
        day, month, year = int(m.group(1)), int(m.group(2)), _year(m.group(3), today)
        if _valid(day, month, year):
            found.append((m.start(), (day, month, year)))
    for m in re.finditer(r"\b(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?", t):
        month = next((n for stem, n in _MONTHS if m.group(2).startswith(stem)), None)
        if not month:
            continue
        day, year = int(m.group(1)), _year(m.group(3), today)
        if _valid(day, month, year):
            found.append((m.start(), (day, month, year)))
    # «Не 12, а 14 марта»: первое число без месяца берёт месяц у второго — нам нужно последнее.
    if found:
        return sorted(found)[-1][1]
    # «Завтра», «через неделю», «послезавтра», «через 3 дня» — считаем дату от сегодня.
    relative = find_dates(text, today)
    if relative:
        d = relative[-1]
        return d.day, d.month, None
    return None


def next_date(b: Birthday, today: date) -> date:
    """Ближайший день рождения (сегодняшний тоже считается). 29 февраля в обычный год — 28-го."""
    for year in (today.year, today.year + 1):
        day = b.day
        if b.month == 2 and b.day == 29 and not calendar.isleap(year):
            day = 28
        d = date(year, b.month, day)
        if d >= today:
            return d
    raise ValueError("unreachable")


def turning(b: Birthday, today: date) -> Optional[int]:
    """Сколько исполнится в ближайший день рождения (если известен год)."""
    return next_date(b, today).year - b.year if b.year else None


class BirthdayService:
    def __init__(self, repo: BirthdayRepository, user_id: int):
        self.repo = repo
        self.user_id = user_id

    def all(self, today: date) -> list[Birthday]:
        return sorted(self.repo.all(self.user_id), key=lambda b: next_date(b, today))

    def get(self, bday_id: int) -> Optional[Birthday]:
        return self.repo.get(self.user_id, bday_id)

    def of(self, person_id: int) -> Optional[Birthday]:
        return self.repo.by_person(self.user_id, person_id)

    def save(self, person_id: int, day: int, month: int, year: Optional[int]) -> Birthday:
        self.repo.save(self.user_id, person_id, day, month, year)
        saved = self.of(person_id)
        if not saved or (saved.day, saved.month, saved.year) != (day, month, year):
            raise VerificationError("birthday not saved")
        return saved

    def delete(self, b: Birthday) -> None:
        self.repo.delete(self.user_id, b.id)
        if self.get(b.id):
            raise VerificationError("birthday not deleted")

    def on(self, day: date) -> list[Birthday]:
        """Чьи дни рождения выпадают на эту дату."""
        return [b for b in self.repo.all(self.user_id) if next_date(b, day) == day]
