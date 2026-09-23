"""SQL для распорядка: события, правила повторения, уведомления."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .db import Database

EVENT_FIELDS = ("type", "title", "date", "start_time", "end_time", "comment", "location",
                "discipline", "focus", "recurrence_id")
RULE_FIELDS = ("kind", "weekdays", "month_day", "type", "title", "start_time", "end_time", "comment",
               "location", "discipline", "focus", "start_date", "end_date", "generated_until")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Event:
    id: int
    type: str
    title: Optional[str]
    date: date
    start_time: str
    end_time: Optional[str] = None
    comment: Optional[str] = None
    location: Optional[str] = None
    discipline: Optional[str] = None
    focus: Optional[str] = None
    recurrence_id: Optional[int] = None

    def as_values(self) -> dict:
        return {"type": self.type, "title": self.title, "date": self.date.isoformat(),
                "start_time": self.start_time, "end_time": self.end_time, "comment": self.comment,
                "location": self.location, "discipline": self.discipline, "focus": self.focus,
                "recurrence_id": self.recurrence_id}


@dataclass
class Rule:
    id: int
    kind: str
    weekdays: list[int]
    month_day: Optional[int]
    type: str
    title: Optional[str]
    start_time: str
    end_time: Optional[str]
    comment: Optional[str]
    location: Optional[str]
    discipline: Optional[str]
    focus: Optional[str]
    start_date: date
    end_date: Optional[date]
    generated_until: Optional[date]


@dataclass
class Notification:
    id: int
    event_id: Optional[int]
    type: str
    scheduled_at: str
    sent: bool
    cancelled: bool


def _event(r) -> Event:
    return Event(id=r["id"], type=r["type"], title=r["title"], date=date.fromisoformat(r["date"]),
                 start_time=r["start_time"], end_time=r["end_time"], comment=r["comment"],
                 location=r["location"], discipline=r["discipline"], focus=r["focus"],
                 recurrence_id=r["recurrence_id"])


def _rule(r) -> Rule:
    return Rule(
        id=r["id"], kind=r["kind"],
        weekdays=[int(x) for x in (r["weekdays"] or "").split(",") if x != ""],
        month_day=r["month_day"], type=r["type"], title=r["title"], start_time=r["start_time"],
        end_time=r["end_time"], comment=r["comment"], location=r["location"], discipline=r["discipline"],
        focus=r["focus"], start_date=date.fromisoformat(r["start_date"]),
        end_date=date.fromisoformat(r["end_date"]) if r["end_date"] else None,
        generated_until=date.fromisoformat(r["generated_until"]) if r["generated_until"] else None,
    )


class EventRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, values: dict) -> int:
        cols = ", ".join(EVENT_FIELDS)
        marks = ", ".join("?" for _ in EVENT_FIELDS)
        with self.db.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO events (user_id, {cols}, created_at, updated_at) VALUES (?, {marks}, ?, ?)",
                (user_id, *[values.get(f) for f in EVENT_FIELDS], _now(), _now()),
            )
            return cur.lastrowid

    def get(self, user_id: int, event_id: int) -> Optional[Event]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM events WHERE id=? AND user_id=?", (event_id, user_id)).fetchone()
        return _event(r) if r else None

    def between(self, user_id: int, start: date, end: date) -> list[Event]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE user_id=? AND date>=? AND date<=? ORDER BY date, start_time, id",
                (user_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [_event(r) for r in rows]

    def of_rule(self, user_id: int, rule_id: int, since: date) -> list[Event]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE user_id=? AND recurrence_id=? AND date>=? ORDER BY date",
                (user_id, rule_id, since.isoformat()),
            ).fetchall()
        return [_event(r) for r in rows]

    def update(self, user_id: int, event_id: int, values: dict) -> None:
        sets = ", ".join(f"{f}=?" for f in EVENT_FIELDS)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE events SET {sets}, updated_at=? WHERE id=? AND user_id=?",
                (*[values.get(f) for f in EVENT_FIELDS], _now(), event_id, user_id),
            )

    def delete(self, user_id: int, ids: list[int]) -> None:
        if not ids:
            return
        with self.db.connect() as conn:
            conn.executemany("DELETE FROM events WHERE id=? AND user_id=?", [(i, user_id) for i in ids])


class RuleRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, values: dict) -> int:
        cols = ", ".join(RULE_FIELDS)
        marks = ", ".join("?" for _ in RULE_FIELDS)
        with self.db.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO recurrences (user_id, {cols}, created_at, updated_at) VALUES (?, {marks}, ?, ?)",
                (user_id, *[values.get(f) for f in RULE_FIELDS], _now(), _now()),
            )
            return cur.lastrowid

    def get(self, user_id: int, rule_id: int) -> Optional[Rule]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM recurrences WHERE id=? AND user_id=?", (rule_id, user_id)).fetchone()
        return _rule(r) if r else None

    def active(self, user_id: int, today: date) -> list[Rule]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recurrences WHERE user_id=? AND (end_date IS NULL OR end_date>=?)",
                (user_id, today.isoformat()),
            ).fetchall()
        return [_rule(r) for r in rows]

    def update(self, user_id: int, rule_id: int, values: dict) -> None:
        sets = ", ".join(f"{k}=?" for k in values)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE recurrences SET {sets}, updated_at=? WHERE id=? AND user_id=?",
                         (*values.values(), _now(), rule_id, user_id))


class NotificationRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, event_id: Optional[int], ntype: str, scheduled_at: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO notifications (user_id, event_id, type, scheduled_at, created_at) VALUES (?,?,?,?,?)",
                (user_id, event_id, ntype, scheduled_at, _now()),
            )
            return cur.lastrowid

    def cancel_pending_for_events(self, user_id: int, event_ids: list[int]) -> None:
        if not event_ids:
            return
        with self.db.connect() as conn:
            conn.executemany(
                "UPDATE notifications SET cancelled=1 WHERE user_id=? AND event_id=? AND sent=0 AND cancelled=0",
                [(user_id, e) for e in event_ids],
            )

    def pending_until(self, user_id: int, until: str) -> list[Notification]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id=? AND sent=0 AND cancelled=0 AND scheduled_at<=? "
                "ORDER BY scheduled_at",
                (user_id, until),
            ).fetchall()
        return [Notification(r["id"], r["event_id"], r["type"], r["scheduled_at"], bool(r["sent"]),
                             bool(r["cancelled"])) for r in rows]

    def for_day(self, user_id: int, day: date, ntype: str) -> list[Notification]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id=? AND type=? AND scheduled_at LIKE ?",
                (user_id, ntype, day.isoformat() + "%"),
            ).fetchall()
        return [Notification(r["id"], r["event_id"], r["type"], r["scheduled_at"], bool(r["sent"]),
                             bool(r["cancelled"])) for r in rows]

    def mark_sent(self, notif_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE notifications SET sent=1, sent_at=? WHERE id=?", (_now(), notif_id))

    def mark_missed(self, notif_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE notifications SET cancelled=1 WHERE id=?", (notif_id,))
