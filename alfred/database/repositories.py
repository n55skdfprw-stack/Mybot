"""Работа с таблицами. Здесь только SQL, без бизнес-логики."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .db import Database


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Task:
    id: int
    title: str
    due_date: Optional[date]
    completed: bool
    completed_at: Optional[str]


@dataclass
class Note:
    id: int
    title: str
    content: str
    created_at: str


@dataclass
class Context:
    intent: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    missing_parameter: Optional[str] = None
    data: dict = None
    expires_at: Optional[str] = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}


def _task(row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        due_date=date.fromisoformat(row["due_date"]) if row["due_date"] else None,
        completed=bool(row["completed"]),
        completed_at=row["completed_at"],
    )


def _note(row) -> Note:
    return Note(id=row["id"], title=row["title"], content=row["content"], created_at=row["created_at"])


class UserRepository:
    def __init__(self, db: Database):
        self.db = db

    def ensure(self, telegram_id: int, timezone: str, city: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO users (telegram_id, timezone, home_city, current_city, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (telegram_id, timezone, city, city, _now(), _now()),
            )
            return cur.lastrowid

    def city(self, user_id: int) -> Optional[str]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT current_city, home_city FROM users WHERE id=?", (user_id,)).fetchone()
        return (row["current_city"] or row["home_city"]) if row else None

    def set_city(self, user_id: int, city: Optional[str]) -> None:
        """None — вернуться в домашний город."""
        with self.db.connect() as conn:
            conn.execute("UPDATE users SET current_city=COALESCE(?, home_city), updated_at=? WHERE id=?",
                         (city, _now(), user_id))


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, title: str, due: Optional[date]) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (user_id, title, due_date, created_at) VALUES (?,?,?,?)",
                (user_id, title, due.isoformat() if due else None, _now()),
            )
            return cur.lastrowid

    def get(self, user_id: int, task_id: int) -> Optional[Task]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        return _task(row) if row else None

    def active(self, user_id: int) -> list[Task]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND completed=0 "
                "ORDER BY due_date IS NULL, due_date, id",
                (user_id,),
            ).fetchall()
        return [_task(r) for r in rows]

    def completed_since(self, user_id: int, since_iso: str) -> list[Task]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND completed=1 AND completed_at>=? ORDER BY completed_at",
                (user_id, since_iso),
            ).fetchall()
        return [_task(r) for r in rows]

    def update(self, user_id: int, task_id: int, title: str, due: Optional[date]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tasks SET title=?, due_date=? WHERE id=? AND user_id=?",
                (title, due.isoformat() if due else None, task_id, user_id),
            )

    def complete(self, user_id: int, task_ids: list[int], when_iso: str) -> None:
        if not task_ids:
            return
        with self.db.connect() as conn:
            conn.executemany(
                "UPDATE tasks SET completed=1, completed_at=? WHERE id=? AND user_id=?",
                [(when_iso, tid, user_id) for tid in task_ids],
            )

    def recently_completed(self, user_id: int, limit: int = 30) -> list[Task]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND completed=1 ORDER BY completed_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_task(r) for r in rows]

    def restore(self, user_id: int, task_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE tasks SET completed=0, completed_at=NULL WHERE id=? AND user_id=?",
                         (task_id, user_id))

    def delete(self, user_id: int, task_ids: list[int]) -> None:
        if not task_ids:
            return
        with self.db.connect() as conn:
            conn.executemany("DELETE FROM tasks WHERE id=? AND user_id=?", [(tid, user_id) for tid in task_ids])


class NoteRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, title: str, content: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO notes (user_id, title, content, created_at, updated_at) VALUES (?,?,?,?,?)",
                (user_id, title, content, _now(), _now()),
            )
            return cur.lastrowid

    def get(self, user_id: int, note_id: int) -> Optional[Note]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id=? AND user_id=?", (note_id, user_id)).fetchone()
        return _note(row) if row else None

    def all(self, user_id: int) -> list[Note]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM notes WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        return [_note(r) for r in rows]

    def update(self, user_id: int, note_id: int, title: str, content: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE notes SET title=?, content=?, updated_at=? WHERE id=? AND user_id=?",
                (title, content, _now(), note_id, user_id),
            )

    def delete(self, user_id: int, note_ids: list[int]) -> None:
        if not note_ids:
            return
        with self.db.connect() as conn:
            conn.executemany("DELETE FROM notes WHERE id=? AND user_id=?", [(nid, user_id) for nid in note_ids])


class ContextRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, user_id: int) -> Context:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM conversation_context WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return Context()
        return Context(
            intent=row["intent"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            missing_parameter=row["missing_parameter"],
            data=json.loads(row["collected_data"] or "{}"),
            expires_at=row["expires_at"],
        )

    def save(self, user_id: int, ctx: Context) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO conversation_context "
                "(user_id, intent, entity_type, entity_id, missing_parameter, collected_data, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET intent=excluded.intent, entity_type=excluded.entity_type, "
                "entity_id=excluded.entity_id, missing_parameter=excluded.missing_parameter, "
                "collected_data=excluded.collected_data, created_at=excluded.created_at, "
                "expires_at=excluded.expires_at",
                (user_id, ctx.intent, ctx.entity_type, ctx.entity_id, ctx.missing_parameter,
                 json.dumps(ctx.data, ensure_ascii=False), _now(), ctx.expires_at),
            )
