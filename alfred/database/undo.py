"""↩️ Отмена последнего действия: снимок всех записей одного человека до команды и возврат к нему.

Работает с «сырыми» строками базы, поэтому зашифрованные поля так и остаются зашифрованными.
"""

import sqlite3
from typing import Optional

SKIP = {"conversation_context", "ai_usage", "users"}   # служебное — не откатываем


def _tables(conn: sqlite3.Connection) -> list[str]:
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    out = []
    for t in names:
        if t in SKIP or t.startswith("sqlite_"):
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        if "user_id" in cols:
            out.append(t)
    return sorted(out)


def snapshot(path: str, user_id: int) -> dict[str, tuple[list[str], list[tuple]]]:
    conn = sqlite3.connect(path)
    try:
        snap = {}
        for t in _tables(conn):
            cur = conn.execute(f"SELECT * FROM {t} WHERE user_id=? ORDER BY rowid", (user_id,))
            snap[t] = ([d[0] for d in cur.description], cur.fetchall())
        return snap
    finally:
        conn.close()


def same(a: Optional[dict], b: Optional[dict]) -> bool:
    return a is not None and b is not None and a == b


def restore(path: str, user_id: int, snap: dict[str, tuple[list[str], list[tuple]]]) -> None:
    conn = sqlite3.connect(path)          # без PRAGMA foreign_keys — порядок удаления/вставки не важен
    try:
        with conn:
            for t in _tables(conn):
                conn.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
            for t, (cols, rows) in snap.items():
                if not rows:
                    continue
                q = ",".join("?" * len(cols))
                conn.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({q})", rows)
    finally:
        conn.close()
