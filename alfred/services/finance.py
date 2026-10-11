"""Бизнес-логика финансов и долгов."""

import os
from datetime import date
from typing import Optional

from ..database.finance_repo import (Debt, DebtRepository, Operation, OperationRepository, PeopleRepository,
                                     Person)
from . import search
from .tasks import VerificationError


class FinanceService:
    def __init__(self, ops: OperationRepository, user_id: int):
        self.ops = ops
        self.user_id = user_id

    def add(self, op_type: str, amount: float, category: Optional[str], description: Optional[str], day: date,
            original_amount: Optional[float] = None, original_currency: Optional[str] = None) -> Operation:
        op_id = self.ops.add(self.user_id, op_type, amount, category, description, day, original_amount,
                             original_currency)
        op = self.ops.get(self.user_id, op_id)
        if not op or abs(op.amount - amount) > 0.001:
            raise VerificationError("operation not saved")
        return op

    def get(self, op_id: int) -> Optional[Operation]:
        return self.ops.get(self.user_id, op_id)

    def latest(self, op_type: Optional[str] = None, limit: int = 10) -> list[Operation]:
        return self.ops.latest(self.user_id, op_type, limit)

    def between(self, start: date, end: date, op_type: Optional[str] = None) -> list[Operation]:
        return self.ops.between(self.user_id, start, end, op_type)

    def balance(self) -> float:
        income, expense = self.ops.totals(self.user_id)
        return round(income - expense, 2)

    def find(self, query: str, op_type: Optional[str] = None) -> list[Operation]:
        pool = self.latest(op_type, limit=200)
        return search.best_matches(query, pool, lambda o: f"{o.category or ''} {o.description or ''}")

    def update(self, op: Operation, amount: Optional[float] = None, category: Optional[str] = None,
               original: Optional[tuple[float, str]] = None) -> Operation:
        new_amount = op.amount if amount is None else amount
        new_category = category or op.category
        orig_amount, orig_cur = (original if original else
                                 (None, None) if amount is not None else (op.original_amount, op.original_currency))
        self.ops.update(self.user_id, op.id, new_amount, new_category, op.description, orig_amount, orig_cur)
        updated = self.ops.get(self.user_id, op.id)
        if not updated or abs(updated.amount - new_amount) > 0.001:
            raise VerificationError("operation not updated")
        return updated

    def delete(self, ops: list[Operation]) -> None:
        self.ops.delete(self.user_id, [o.id for o in ops])
        for o in ops:
            if self.ops.get(self.user_id, o.id):
                raise VerificationError("operation not deleted")


def _same_name(a: str, b: str) -> bool:
    a, b = search.normalize(a), search.normalize(b)
    if min(len(a), len(b)) < 3:
        return a == b
    common = len(os.path.commonprefix([a, b]))
    return common >= max(3, min(len(a), len(b)) - 2)


# Уменьшительные имена: «Серёга» и «Сергей» — один человек (только если точного совпадения нет).
NICKS = [
    ("сергей", "серега", "сережа", "серый", "серж"), ("александр", "саша", "саня", "шура", "санек"),
    ("александра", "саша", "шура"), ("дмитрий", "дима", "димон", "митя"), ("евгений", "женя", "жека"),
    ("евгения", "женя"), ("алексей", "леша", "леха", "алеша"), ("михаил", "миша", "мишаня"),
    ("николай", "коля", "колян"), ("владимир", "вова", "володя", "вован"), ("павел", "паша", "пашка"),
    ("петр", "петя"), ("иван", "ваня", "ванек"), ("мария", "маша", "маня"), ("екатерина", "катя"),
    ("анастасия", "настя"), ("елена", "лена"), ("татьяна", "таня"), ("ольга", "оля"), ("юлия", "юля"),
    ("наталья", "наташа"), ("константин", "костя"), ("андрей", "андрюха", "андрюша"),
    ("максим", "макс"), ("артем", "тема", "темыч"), ("даниил", "даня", "данила"), ("георгий", "гоша", "жора"),
    ("вячеслав", "слава"), ("станислав", "стас", "слава"), ("илья", "илюха", "илюша"),
    ("анна", "аня", "анюта"), ("ксения", "ксюша"), ("дарья", "даша"), ("виктория", "вика"),
    ("светлана", "света"), ("людмила", "люда"), ("валентина", "валя"), ("кирилл", "киря"),
    ("роман", "рома"), ("антон", "тоха"), ("игорь", "гоша"), ("олег", "олежа"), ("тимофей", "тима"),
]


def _forms(name: str) -> set[str]:
    """Все варианты имени: само имя + полное/уменьшительные из одной группы."""
    out = {name}
    for group in NICKS:
        if any(_same_name(name, g) for g in group):
            out.update(group)
    return out


def _loose_same(stored: str, said: str) -> bool:
    """Мягкое сравнение: уменьшительные имена и опечатки («Сергй»)."""
    said_n = search.normalize(said)
    if any(_same_name(stored, f) for f in _forms(said_n)) or any(_same_name(said_n, f) for f in _forms(
            search.normalize(stored))):
        return True
    return search.typo_close(search.normalize(stored), said_n)


class DebtService:
    def __init__(self, debts: DebtRepository, people: PeopleRepository, user_id: int):
        self.debts = debts
        self.people = people
        self.user_id = user_id

    def find_people(self, name: str) -> list[Person]:
        words = name.split()
        first = words[0]
        last = words[1] if len(words) > 1 else None
        people = self.people.all(self.user_id)
        if len(words) == 1 and len(first) >= 7:
            # «иванпетров» — имя и фамилия слитно
            joined = [p for p in people if p.last_name and
                      search.typo_close(search.normalize(p.first_name + p.last_name), search.normalize(first))]
            if joined:
                return joined
        found = [p for p in people if _same_name(p.first_name, first)]
        if not found:                      # точно не нашли — пробуем «Серёга» → Сергей и опечатки
            found = [p for p in people if _loose_same(p.first_name, first)]
        if last:
            found = [p for p in found if p.last_name and (_same_name(p.last_name, last) or search.typo_close(
                search.normalize(p.last_name), search.normalize(last)))] or found
        return found

    def person(self, person_id: int) -> Optional[Person]:
        return self.people.get(self.user_id, person_id)

    def create_person(self, name: str) -> Person:
        words = [w[:1].upper() + w[1:] for w in name.split()]
        pid = self.people.add(self.user_id, words[0], " ".join(words[1:]) or None)
        return self.people.get(self.user_id, pid)

    def get(self, person: Person, direction: str) -> Optional[Debt]:
        return self.debts.find(self.user_id, person.id, direction)

    def add(self, person: Person, direction: str, amount: float) -> float:
        current = self.get(person, direction)
        total = round((current.amount if current else 0) + amount, 2)
        self.debts.set_amount(self.user_id, person.id, direction, total)
        check = self.get(person, direction)
        if not check or abs(check.amount - total) > 0.001:
            raise VerificationError("debt not saved")
        return total

    def repay(self, person: Person, direction: str, amount: Optional[float]) -> tuple[float, float]:
        """Возвращает (сколько погашено, сколько осталось). amount=None — погашено полностью."""
        current = self.get(person, direction)
        if not current:
            return 0.0, 0.0
        paid = current.amount if amount is None else min(amount, current.amount)
        left = round(current.amount - paid, 2)
        self.debts.set_amount(self.user_id, person.id, direction, left)
        check = self.get(person, direction)
        if (left > 0 and (not check or abs(check.amount - left) > 0.001)) or (left <= 0 and check):
            raise VerificationError("debt not updated")
        return paid, left

    def by_id(self, debt_id: int) -> Optional[Debt]:
        return self.debts.get(self.user_id, debt_id)

    def set_total(self, person: Person, direction: str, amount: float) -> Optional[Debt]:
        """Исправление: сумма долга становится ровно такой (а не прибавляется)."""
        self.debts.set_amount(self.user_id, person.id, direction, round(amount, 2))
        check = self.get(person, direction)
        if not check or abs(check.amount - amount) > 0.001:
            raise VerificationError("debt not corrected")
        return check

    def remove(self, person: Person, direction: str) -> None:
        self.debts.set_amount(self.user_id, person.id, direction, 0)

    def active(self, direction: Optional[str] = None) -> list[tuple[Person, Debt]]:
        result = []
        for d in self.debts.active(self.user_id, direction):
            p = self.person(d.person_id)
            if p:
                result.append((p, d))
        return result
