"""Хранилище дней рождения. Каждый день рождения привязан к человеку из таблицы people."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Birthday:
    id: int
    person_id: int
    day: int
    month: int
    year: Optional[int]


def _row(r) -> Birthday:
    return Birthday(r["id"], r["person_id"], r["day"], r["month"], r["year"])


class BirthdayRepository:
    def __init__(self, db: Database):
        self.db = db

    def all(self, user_id: int) -> list[Birthday]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM birthdays WHERE user_id=?", (user_id,)).fetchall()
        return [_row(r) for r in rows]

    def get(self, user_id: int, bday_id: int) -> Optional[Birthday]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM birthdays WHERE id=? AND user_id=?", (bday_id, user_id)).fetchone()
        return _row(r) if r else None

    def by_person(self, user_id: int, person_id: int) -> Optional[Birthday]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM birthdays WHERE person_id=? AND user_id=?",
                             (person_id, user_id)).fetchone()
        return _row(r) if r else None

    def save(self, user_id: int, person_id: int, day: int, month: int, year: Optional[int]) -> None:
        """Записывает или заменяет день рождения человека (у человека он один)."""
        with self.db.connect() as conn:
            existing = conn.execute("SELECT id FROM birthdays WHERE person_id=? AND user_id=?",
                                    (person_id, user_id)).fetchone()
            if existing:
                conn.execute("UPDATE birthdays SET day=?, month=?, year=?, updated_at=? WHERE id=?",
                             (day, month, year, _now(), existing["id"]))
            else:
                conn.execute("INSERT INTO birthdays (user_id, person_id, day, month, year, created_at, updated_at) "
                             "VALUES (?,?,?,?,?,?,?)", (user_id, person_id, day, month, year, _now(), _now()))

    def delete(self, user_id: int, bday_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM birthdays WHERE id=? AND user_id=?", (bday_id, user_id))
