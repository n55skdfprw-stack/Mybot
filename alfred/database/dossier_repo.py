"""Досье — это те же люди из таблицы people, только со всеми полями.

Один человек = одна запись: долги и дни рождения ссылаются на неё же.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .db import Database

TEXT_FIELDS = ("phone", "address", "job", "interests", "preferences", "likes_dislikes", "important_facts")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Card:
    id: int
    first_name: str
    last_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    job: Optional[str] = None
    interests: Optional[str] = None
    preferences: Optional[str] = None
    likes_dislikes: Optional[str] = None
    important_facts: Optional[str] = None
    hidden: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}" if self.last_name else self.first_name

    def haystack(self) -> str:
        return " ".join(filter(None, [self.first_name, self.last_name] + [getattr(self, f) for f in TEXT_FIELDS]))


def _card(r) -> Card:
    return Card(r["id"], r["first_name"], r["last_name"], *[r[f] for f in TEXT_FIELDS],
                hidden=bool(r["dossier_hidden"]))


# Человек «пустой», если о нём ничего не записано, досье не заводили явно и у него нет ни долга,
# ни дня рождения. Такие остаются после удаления долга или дня рождения — их убираем.
_EMPTY = " AND ".join(f"COALESCE(TRIM({f}), '') = ''" for f in TEXT_FIELDS)
_ORPHAN = (f"user_id=? AND dossier_explicit=0 AND {_EMPTY} "
           "AND id NOT IN (SELECT person_id FROM debts WHERE user_id=people.user_id) "
           "AND id NOT IN (SELECT person_id FROM birthdays WHERE user_id=people.user_id)")


class DossierRepository:
    def __init__(self, db: Database):
        self.db = db

    def purge_orphans(self, user_id: int) -> int:
        with self.db.connect() as conn:
            return conn.execute(f"DELETE FROM people WHERE {_ORPHAN}", (user_id,)).rowcount

    def all(self, user_id: int) -> list[Card]:
        self.purge_orphans(user_id)
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM people WHERE user_id=? AND dossier_hidden=0 "
                                "ORDER BY first_name, last_name", (user_id,)).fetchall()
        return [_card(r) for r in rows]

    def mark_explicit(self, user_id: int, person_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE people SET dossier_explicit=1, dossier_hidden=0, updated_at=? "
                         "WHERE id=? AND user_id=?", (_now(), person_id, user_id))

    def get(self, user_id: int, person_id: int) -> Optional[Card]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM people WHERE id=? AND user_id=?", (person_id, user_id)).fetchone()
        return _card(r) if r else None

    def set(self, user_id: int, person_id: int, values: dict) -> None:
        """Меняет поля и снова показывает человека в досье, если досье было удалено."""
        allowed = {k: v for k, v in values.items() if k in TEXT_FIELDS + ("first_name", "last_name")}
        with self.db.connect() as conn:
            for k, v in allowed.items():
                conn.execute(f"UPDATE people SET {k}=?, dossier_hidden=0, updated_at=? WHERE id=? AND user_id=?",
                             (v, _now(), person_id, user_id))
            if not allowed:
                conn.execute("UPDATE people SET dossier_hidden=0, updated_at=? WHERE id=? AND user_id=?",
                             (_now(), person_id, user_id))

    def remove(self, user_id: int, person_id: int) -> None:
        """Удаляет досье. Если у человека есть долг или день рождения — он остаётся, но без личных данных."""
        with self.db.connect() as conn:
            linked = conn.execute(
                "SELECT (SELECT COUNT(*) FROM debts WHERE person_id=? AND user_id=?) + "
                "(SELECT COUNT(*) FROM birthdays WHERE person_id=? AND user_id=?) AS n",
                (person_id, user_id, person_id, user_id)).fetchone()["n"]
            if linked:
                sets = ", ".join(f"{f}=NULL" for f in TEXT_FIELDS)
                conn.execute(f"UPDATE people SET {sets}, dossier_hidden=1, dossier_explicit=0, updated_at=? "
                             "WHERE id=? AND user_id=?",
                             (_now(), person_id, user_id))
            else:
                conn.execute("DELETE FROM people WHERE id=? AND user_id=?", (person_id, user_id))
