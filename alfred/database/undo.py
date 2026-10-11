"""↩️ Отмена последнего действия: снимки записей одного человека до и после команды; отмена возвращает
только то, что команда поменяла.

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


def revert(path: str, user_id: int, before: dict, after: dict) -> None:
    """Откатить ровно то, что поменяла одна команда (разница «до» и «после» неё), не трогая всё,
    что случилось позже само (бот отметил отправленные напоминания, ночью продлил расписание и т.п.)."""
    conn = sqlite3.connect(path)          # без PRAGMA foreign_keys — порядок удаления/вставки не важен
    try:
        with conn:
            for t in sorted(set(before) | set(after)):
                cols, old_rows = before.get(t) or after[t]
                new_rows = (after.get(t) or (cols, []))[1]
                if "id" not in cols:
                    continue
                i = cols.index("id")
                old = {r[i]: r for r in old_rows}
                new = {r[i]: r for r in new_rows}
                for row_id in new.keys() - old.keys():             # команда добавила — убираем
                    conn.execute(f"DELETE FROM {t} WHERE id=? AND user_id=?", (row_id, user_id))
                back = [r for row_id, r in old.items() if new.get(row_id) != r]   # удалила или изменила
                if back:
                    q = ",".join("?" * len(cols))
                    conn.executemany(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({q})", back)
    finally:
        conn.close()
