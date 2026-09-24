"""Подключение к SQLite и создание таблиц (миграции)."""

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    name TEXT,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    home_city TEXT NOT NULL DEFAULT 'Санкт-Петербург',
    current_city TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    due_date TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, completed);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);

CREATE TABLE IF NOT EXISTS conversation_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    intent TEXT,
    entity_type TEXT,
    entity_id INTEGER,
    missing_parameter TEXT,
    collected_data TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS recurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,              -- weekly | monthly
    weekdays TEXT,                   -- «0,3» для пн и чт
    month_day INTEGER,
    type TEXT NOT NULL,
    title TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT,
    comment TEXT,
    location TEXT,
    discipline TEXT,
    focus TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    generated_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,
    title TEXT,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    comment TEXT,
    location TEXT,
    discipline TEXT,
    focus TEXT,
    recurrence_id INTEGER REFERENCES recurrences(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user_date ON events(user_id, date);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    event_id INTEGER,
    type TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,      -- местное время «ГГГГ-ММ-ДДTЧЧ:ММ»
    sent INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_pending ON notifications(user_id, sent, cancelled, scheduled_at);

CREATE TABLE IF NOT EXISTS financial_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,              -- expense | income
    amount REAL NOT NULL,            -- в рублях
    category TEXT,
    description TEXT,
    original_amount REAL,            -- если была валюта: 50
    original_currency TEXT,          -- «EUR»
    date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fin_user_date ON financial_operations(user_id, date);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    first_name TEXT NOT NULL,
    last_name TEXT,
    phone TEXT,
    address TEXT,
    job TEXT,
    interests TEXT,
    preferences TEXT,
    likes_dislikes TEXT,
    important_facts TEXT,
    dossier_hidden INTEGER NOT NULL DEFAULT 0,  -- 1: досье удалено, но человек остался ради долга/дня рождения
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    direction TEXT NOT NULL,         -- owes_me | i_owe
    amount REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS birthdays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    day INTEGER NOT NULL,
    month INTEGER NOT NULL,
    year INTEGER,                    -- год рождения, если известен
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, person_id)
);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Соединение-транзакция: всё внутри либо сохраняется целиком, либо откатывается."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def wipe_user_data(self, user_id: int) -> None:
        """Полная очистка данных пользователя (сам пользователь остаётся). Одна транзакция."""
        with self.connect() as conn:
            for table in ("notifications", "events", "recurrences", "tasks", "notes", "conversation_context",
                          "debts", "financial_operations", "birthdays", "people"):
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # Старые базы: добавляем новые колонки, если их ещё нет.
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(people)")}
            if "dossier_hidden" not in cols:
                conn.execute("ALTER TABLE people ADD COLUMN dossier_hidden INTEGER NOT NULL DEFAULT 0")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] < SCHEMA_VERSION:
                conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        log.info("База данных готова: %s", self.path)
