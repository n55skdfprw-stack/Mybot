"""Резервные копии базы: снимок «на ходу», целиком зашифрованный ключом DATA_KEY, и восстановление из него."""

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..database.db import Database

MAGIC = b"ALFRED-BACKUP-1\n"          # чтобы отличать нашу копию от любого другого файла
KEEP = 2                              # сколько последних копий хранить в канале
COUNTED = [("tasks", "дела"), ("notes", "заметки"), ("events", "события"), ("financial_operations", "финансы"),
           ("birthdays", "дни рождения"), ("people", "люди"), ("med_cases", "медкарта"), ("users", "пользователи")]


class BackupError(Exception):
    pass


@dataclass
class Snapshot:
    data: bytes
    filename: str
    summary: str


def _counts(path: str) -> str:
    conn = sqlite3.connect(path)
    try:
        parts = []
        for table, label in COUNTED:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue
            parts.append(f"{label} {n}")
        return ", ".join(parts)
    finally:
        conn.close()


def make_backup(db: Database, now: datetime) -> Snapshot:
    """Копия базы без остановки бота (встроенное «горячее» копирование SQLite), зашифрованная целиком."""
    if not db.cipher.on:
        raise BackupError("no_key")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "copy.db")
        src = sqlite3.connect(db.path)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        summary = _counts(path)
        raw = open(path, "rb").read()
    data = MAGIC + db.cipher.fernet.encrypt(raw)
    return Snapshot(data, f"alfred-backup-{now:%Y-%m-%d-%H%M}.bin", summary)


def read_backup(db: Database, data: bytes) -> tuple[bytes, str]:
    """Расшифровать и проверить копию. Возвращает (файл базы, что внутри)."""
    if not db.cipher.on:
        raise BackupError("no_key")
    if not data.startswith(MAGIC):
        raise BackupError("not_backup")
    try:
        raw = db.cipher.fernet.decrypt(data[len(MAGIC):])
    except Exception:
        raise BackupError("wrong_key")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "check.db")
        open(path, "wb").write(raw)
        conn = sqlite3.connect(path)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            has_users = conn.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
        except sqlite3.Error:
            ok, has_users = False, None
        finally:
            conn.close()
        if not ok or not has_users:
            raise BackupError("broken")
        return raw, _counts(path)


def restore_backup(db: Database, raw: bytes) -> None:
    """Заменить текущую базу копией — тем же «горячим» копированием, без остановки бота."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "restore.db")
        open(path, "wb").write(raw)
        src = sqlite3.connect(path)
        dst = sqlite3.connect(db.path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    db.migrate()                     # копия могла быть от старой версии — добавим новые колонки


ERRORS = {
    "no_key": "Шифрование выключено (нет DATA_KEY) — без ключа копии не делаю, чтобы не хранить данные открыто.",
    "not_backup": "Это не резервная копия Альфреда.",
    "wrong_key": "Копия сделана с другим ключом DATA_KEY — открыть её этим ключом нельзя.",
    "broken": "Файл копии повреждён.",
}
