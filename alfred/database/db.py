"""Подключение к SQLite и создание таблиц (миграции)."""

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)

SCHEMA_VERSION = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    name TEXT,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    home_city TEXT NOT NULL DEFAULT 'Санкт-Петербург',
    current_city TEXT,
    username TEXT,                   -- @имя в Telegram (без @, маленькими буквами)
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',       -- owner / user
    status TEXT NOT NULL DEFAULT 'active',   -- active / blocked
    ai_limit INTEGER,                -- запросов к ИИ в день; NULL — по умолчанию
    address TEXT,                    -- как обращаться: «Сэр» или «Мэм»; NULL — ещё не спросили
    paused INTEGER NOT NULL DEFAULT 0,  -- 1: «⏸ Пауза» — Альфред молчит для этого человека
    last_seen TEXT,
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
    dossier_explicit INTEGER NOT NULL DEFAULT 0,  -- 1: досье заведено явно («Создай досье на …»)
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

-- Медкарта: болезни, лекарства, напоминания о приёме, аллергии, врачи.
CREATE TABLE IF NOT EXISTS med_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,             -- болезнь: «Ангина»
    started TEXT NOT NULL,           -- YYYY-MM-DD
    ended TEXT,                      -- дата выздоровления, NULL — болею сейчас
    doctor TEXT,
    notes TEXT,                      -- другие назначения: «полоскать горло»
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS med_drugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    case_id INTEGER NOT NULL REFERENCES med_cases(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    dose TEXT,                       -- «1 таблетка», «5 капель», «500 мг»
    per_day INTEGER,
    times TEXT,                      -- «08:00,20:00»
    meal TEXT,                       -- «до еды», «после еды», «во время еды»
    days INTEGER,
    start_date TEXT NOT NULL,
    end_date TEXT,                   -- последний день курса
    stopped INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS med_doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    drug_id INTEGER NOT NULL REFERENCES med_drugs(id) ON DELETE CASCADE,
    due_at TEXT NOT NULL,            -- ISO: когда напомнить
    status TEXT NOT NULL,            -- pending (отложено), sent, taken
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_med_doses ON med_doses (drug_id, due_at);

CREATE TABLE IF NOT EXISTS med_allergies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS med_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,              -- «Иванова Анна Петровна» или «Поликлиника №5»
    specialty TEXT,                  -- «терапевт»
    phone TEXT,
    place TEXT,                      -- клиника, адрес
    created_at TEXT NOT NULL
);

-- Режимы доступа: приглашения и счётчик запросов к ИИ.
CREATE TABLE IF NOT EXISTS invites (
    username TEXT PRIMARY KEY,       -- без @, маленькими буквами
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,            -- например, stopped_for_all
    value TEXT
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
                          "debts", "financial_operations", "birthdays", "people",
                          "med_doses", "med_drugs", "med_cases", "med_allergies", "med_contacts"):
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

    def delete_user(self, user_id: int) -> None:
        """Удалить пользователя вместе со всеми его данными."""
        self.wipe_user_data(user_id)
        with self.connect() as conn:
            conn.execute("DELETE FROM ai_usage WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # Старые базы: добавляем новые колонки, если их ещё нет.
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(people)")}
            if "dossier_hidden" not in cols:
                conn.execute("ALTER TABLE people ADD COLUMN dossier_hidden INTEGER NOT NULL DEFAULT 0")
            if "dossier_explicit" not in cols:
                conn.execute("ALTER TABLE people ADD COLUMN dossier_explicit INTEGER NOT NULL DEFAULT 0")
            ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
            for col, ddl in (("username", "TEXT"), ("display_name", "TEXT"),
                             ("role", "TEXT NOT NULL DEFAULT 'user'"), ("status", "TEXT NOT NULL DEFAULT 'active'"),
                             ("ai_limit", "INTEGER"), ("last_seen", "TEXT"), ("address", "TEXT"),
                             ("paused", "INTEGER NOT NULL DEFAULT 0")):
                if col not in ucols:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] < SCHEMA_VERSION:
                conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        log.info("База данных готова: %s", self.path)
