"""Шифрование личных данных в базе (дела, заметки, распорядок, финансы, досье, медкарта).

Работает незаметно для остального кода: при записи нужные столбцы шифруются, при чтении — расшифровываются.
Ключ хранится ОТДЕЛЬНО от базы — в переменной окружения DATA_KEY (на Railway). Если файл базы или её
резервная копия утечёт, без ключа прочитать записи нельзя.
"""

import logging
import re
import sqlite3
from typing import Any, Optional

log = logging.getLogger(__name__)

PREFIX = "enc1:"

# Какие столбцы шифруем. Даты, суммы, номера и служебные поля — нет: по ним база ищет и сортирует.
SENSITIVE: dict[str, set[str]] = {
    "tasks": {"title"},
    "notes": {"title", "content"},
    "conversation_context": {"collected_data"},
    "recurrences": {"title", "comment", "location", "discipline", "focus"},
    "events": {"title", "comment", "location", "discipline", "focus"},
    "financial_operations": {"description"},
    "people": {"first_name", "last_name", "phone", "address", "job", "interests", "preferences",
               "likes_dislikes", "important_facts"},
    "med_cases": {"title", "doctor", "notes"},
    "med_drugs": {"name", "dose", "meal", "note"},
    "med_allergies": {"text"},
    "med_contacts": {"name", "specialty", "phone", "place"},
}


class Cipher:
    def __init__(self, key: Optional[str]):
        self.fernet = None
        if key:
            from cryptography.fernet import Fernet
            self.fernet = Fernet(key.strip().encode())

    @property
    def on(self) -> bool:
        return self.fernet is not None

    def enc(self, value: Any) -> Any:
        if not self.on or not isinstance(value, str) or not value or value.startswith(PREFIX):
            return value
        return PREFIX + self.fernet.encrypt(value.encode()).decode()

    def dec(self, value: Any) -> Any:
        if not isinstance(value, str) or not value.startswith(PREFIX):
            return value
        if not self.on:
            return "🔒"
        try:
            return self.fernet.decrypt(value[len(PREFIX):].encode()).decode()
        except Exception:
            log.error("Не удалось расшифровать запись — ключ DATA_KEY не подходит?")
            return "🔒"


def new_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ------------------------------------------------------------------ разбор запросов при записи
_INSERT = re.compile(r"^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)", re.I | re.S)
_UPDATE = re.compile(r"^\s*UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\s+WHERE\s+|$)", re.I | re.S)


def _split(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _positions(sql: str) -> dict[int, str]:
    """Какой по счёту «?» относится к какому шифруемому столбцу."""
    m = _INSERT.match(sql)
    if m:
        table = m.group(1).lower()
        wanted = SENSITIVE.get(table)
        if not wanted:
            return {}
        cols = [c.strip().lower() for c in m.group(2).split(",")]
        vals = _split(m.group(3))
        res, q = {}, 0
        for col, val in zip(cols, vals):
            n = val.count("?")
            if n == 1 and val.strip() == "?" and col in wanted:
                res[q] = col
            q += n
        return res
    m = _UPDATE.match(sql)
    if m:
        table = m.group(1).lower()
        wanted = SENSITIVE.get(table)
        if not wanted:
            return {}
        res, q = {}, 0
        for part in _split(m.group(2)):
            col, _, val = part.partition("=")
            col, val = col.strip().lower(), val.strip()
            if val == "?" and col in wanted:
                res[q] = col
            q += val.count("?")
        return res
    return {}


def encrypt_params(cipher: Cipher, sql: str, params):
    if not cipher.on or not params or not isinstance(params, (list, tuple)):
        return params
    pos = _positions(sql)
    if not pos:
        return params
    return tuple(cipher.enc(p) if i in pos else p for i, p in enumerate(params))


class SecureConnection(sqlite3.Connection):
    """Соединение, которое само шифрует при записи. Расшифровка — в SecureRow."""
    cipher: Cipher = Cipher(None)

    def execute(self, sql, params=(), /):
        return super().execute(sql, encrypt_params(self.cipher, sql, params))

    def executemany(self, sql, seq, /):
        return super().executemany(sql, [encrypt_params(self.cipher, sql, p) for p in seq])


class SecureRow(sqlite3.Row):
    cipher: Cipher = Cipher(None)

    def __getitem__(self, key):
        return self.cipher.dec(super().__getitem__(key))
