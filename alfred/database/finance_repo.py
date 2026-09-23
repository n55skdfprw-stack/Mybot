"""SQL для финансов, долгов и людей."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .db import Database


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Operation:
    id: int
    type: str
    amount: float
    category: Optional[str]
    description: Optional[str]
    original_amount: Optional[float]
    original_currency: Optional[str]
    date: date
    created_at: str


@dataclass
class Person:
    id: int
    first_name: str
    last_name: Optional[str]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}" if self.last_name else self.first_name


@dataclass
class Debt:
    id: int
    person_id: int
    direction: str
    amount: float


def _op(r) -> Operation:
    return Operation(r["id"], r["type"], r["amount"], r["category"], r["description"], r["original_amount"],
                     r["original_currency"], date.fromisoformat(r["date"]), r["created_at"])


class OperationRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, op_type: str, amount: float, category: Optional[str], description: Optional[str],
            day: date, original_amount: Optional[float] = None, original_currency: Optional[str] = None) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO financial_operations (user_id, type, amount, category, description, original_amount, "
                "original_currency, date, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user_id, op_type, amount, category, description, original_amount, original_currency,
                 day.isoformat(), _now(), _now()),
            )
            return cur.lastrowid

    def get(self, user_id: int, op_id: int) -> Optional[Operation]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM financial_operations WHERE id=? AND user_id=?", (op_id, user_id)).fetchone()
        return _op(r) if r else None

    def between(self, user_id: int, start: date, end: date, op_type: Optional[str] = None) -> list[Operation]:
        sql = "SELECT * FROM financial_operations WHERE user_id=? AND date>=? AND date<=?"
        args = [user_id, start.isoformat(), end.isoformat()]
        if op_type:
            sql += " AND type=?"
            args.append(op_type)
        with self.db.connect() as conn:
            rows = conn.execute(sql + " ORDER BY date DESC, id DESC", args).fetchall()
        return [_op(r) for r in rows]

    def latest(self, user_id: int, op_type: Optional[str] = None, limit: int = 10) -> list[Operation]:
        sql = "SELECT * FROM financial_operations WHERE user_id=?"
        args: list = [user_id]
        if op_type:
            sql += " AND type=?"
            args.append(op_type)
        with self.db.connect() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [_op(r) for r in rows]

    def totals(self, user_id: int) -> tuple[float, float]:
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount END),0) AS inc, "
                "COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) AS exp "
                "FROM financial_operations WHERE user_id=?", (user_id,)).fetchone()
        return float(r["inc"]), float(r["exp"])

    def update(self, user_id: int, op_id: int, amount: float, category: Optional[str], description: Optional[str],
               original_amount: Optional[float], original_currency: Optional[str]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE financial_operations SET amount=?, category=?, description=?, original_amount=?, "
                "original_currency=?, updated_at=? WHERE id=? AND user_id=?",
                (amount, category, description, original_amount, original_currency, _now(), op_id, user_id),
            )

    def delete(self, user_id: int, ids: list[int]) -> None:
        with self.db.connect() as conn:
            conn.executemany("DELETE FROM financial_operations WHERE id=? AND user_id=?", [(i, user_id) for i in ids])


class PeopleRepository:
    def __init__(self, db: Database):
        self.db = db

    def all(self, user_id: int) -> list[Person]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id, first_name, last_name FROM people WHERE user_id=? ORDER BY first_name",
                                (user_id,)).fetchall()
        return [Person(r["id"], r["first_name"], r["last_name"]) for r in rows]

    def get(self, user_id: int, person_id: int) -> Optional[Person]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT id, first_name, last_name FROM people WHERE id=? AND user_id=?",
                             (person_id, user_id)).fetchone()
        return Person(r["id"], r["first_name"], r["last_name"]) if r else None

    def add(self, user_id: int, first_name: str, last_name: Optional[str] = None) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO people (user_id, first_name, last_name, created_at, updated_at) VALUES (?,?,?,?,?)",
                (user_id, first_name, last_name, _now(), _now()))
            return cur.lastrowid


class DebtRepository:
    def __init__(self, db: Database):
        self.db = db

    def active(self, user_id: int, direction: Optional[str] = None) -> list[Debt]:
        sql = "SELECT * FROM debts WHERE user_id=? AND amount>0"
        args: list = [user_id]
        if direction:
            sql += " AND direction=?"
            args.append(direction)
        with self.db.connect() as conn:
            rows = conn.execute(sql + " ORDER BY amount DESC", args).fetchall()
        return [Debt(r["id"], r["person_id"], r["direction"], r["amount"]) for r in rows]

    def find(self, user_id: int, person_id: int, direction: str) -> Optional[Debt]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM debts WHERE user_id=? AND person_id=? AND direction=?",
                             (user_id, person_id, direction)).fetchone()
        return Debt(r["id"], r["person_id"], r["direction"], r["amount"]) if r else None

    def set_amount(self, user_id: int, person_id: int, direction: str, amount: float) -> None:
        """Сохраняет сумму долга. Ноль — долг погашен, запись удаляется."""
        with self.db.connect() as conn:
            existing = conn.execute("SELECT id FROM debts WHERE user_id=? AND person_id=? AND direction=?",
                                    (user_id, person_id, direction)).fetchone()
            if amount <= 0:
                conn.execute("DELETE FROM debts WHERE user_id=? AND person_id=? AND direction=?",
                             (user_id, person_id, direction))
            elif existing:
                conn.execute("UPDATE debts SET amount=?, updated_at=? WHERE id=?", (amount, _now(), existing["id"]))
            else:
                conn.execute("INSERT INTO debts (user_id, person_id, direction, amount, created_at, updated_at) "
                             "VALUES (?,?,?,?,?,?)", (user_id, person_id, direction, amount, _now(), _now()))
