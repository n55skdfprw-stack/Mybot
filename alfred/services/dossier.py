"""Досье: добавление и удаление сведений о человеке, поиск по всему, что записано."""

import re
from typing import Optional

from ..database.dossier_repo import Card, DossierRepository
from . import search
from .tasks import VerificationError

SINGLE = ("phone", "address", "job", "first_name", "last_name")   # новое значение заменяет старое
LISTS = ("interests", "preferences", "facts", "likes", "dislikes")  # новое значение добавляется к списку
KEYS = SINGLE + LISTS
COLUMN = {"facts": "important_facts", "likes": "likes_dislikes", "dislikes": "likes_dislikes"}
PREFIX = {"likes": "+ ", "dislikes": "- "}

PHONE_RE = re.compile(r"(?:\+7|\b8|\b7)?[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b|\+\d[\d\s\-()]{7,}\d")


def find_phone(text: Optional[str]) -> Optional[str]:
    """Телефон из сообщения как есть (ИИ может перепутать цифры, поэтому берём их из текста)."""
    if not text:
        return None
    m = PHONE_RE.search(text)
    return format_phone(m.group(0)) if m else None


def format_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] in "78":
        d = "7" + digits[1:]
        return f"+{d[0]} {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:11]}"
    return raw.strip()


def lines(value: Optional[str]) -> list[str]:
    return [x.strip() for x in (value or "").split("\n") if x.strip()]


def _cap(s: str) -> str:
    s = s.strip().rstrip(".!")
    return s[:1].upper() + s[1:]


def _same(a: str, b: str) -> bool:
    return search.score(b, a) > 0 or search.normalize(b) in search.normalize(a)


class DossierService:
    def __init__(self, repo: DossierRepository, user_id: int):
        self.repo = repo
        self.user_id = user_id

    def all(self) -> list[Card]:
        return self.repo.all(self.user_id)

    def get(self, person_id: int) -> Optional[Card]:
        return self.repo.get(self.user_id, person_id)

    def show(self, person_id: int) -> Optional[Card]:
        """Сделать человека видимым в досье (например, «Создай досье на Сергея», если он уже есть в долгах)."""
        self.repo.set(self.user_id, person_id, {})
        return self.get(person_id)

    def search(self, query: str) -> list[Card]:
        q = search.normalize(query).strip()
        if not q:
            return []
        scored = [(search.score(q, c.haystack()), c) for c in self.all()]
        best = max((s for s, _ in scored), default=0)
        if best:
            return [c for s, c in scored if s == best]
        return [c for c in self.all() if q in search.normalize(c.haystack())]

    def apply(self, card: Card, changes: dict, remove: bool = False) -> tuple[Card, list[str]]:
        """Меняет досье. Возвращает новую карточку и список того, что изменилось (для ответа)."""
        values: dict = {}
        done: list[str] = []
        for key in KEYS:
            raw = changes.get(key)
            if raw is None:
                continue
            value = str(raw).strip()
            col = COLUMN.get(key, key)
            if key in SINGLE:
                if remove:
                    if key in ("first_name",):
                        continue
                    values[col] = None
                    done.append(key)
                elif value:
                    values[col] = format_phone(value) if key == "phone" else _cap(value)
                    done.append(key)
                continue
            current = lines(values.get(col, getattr(card, col)))
            prefix = PREFIX.get(key, "")
            if remove:
                kept = [x for x in current if not (x.startswith(prefix) and _same(x[len(prefix):], value))] \
                    if prefix else [x for x in current if not _same(x, value)]
                if len(kept) != len(current):
                    values[col] = "\n".join(kept) or None
                    done.append(key)
                continue
            for item in re.split(r"\s*[;\n]\s*", value):
                if not item:
                    continue
                entry = prefix + _cap(item)
                # «Любит кофе» после «Не любит кофе» — старое мнение убираем.
                other = {"+ ": "- ", "- ": "+ "}.get(prefix)
                if other:
                    current = [x for x in current if not (x.startswith(other) and _same(x[2:], item))]
                if not any(search.normalize(x) == search.normalize(entry) for x in current):
                    current.append(entry)
            values[col] = "\n".join(current)
            done.append(key)
        if values:
            self.repo.set(self.user_id, card.id, values)
        updated = self.get(card.id)
        if not updated:
            raise VerificationError("dossier not saved")
        return updated, done

    def remove(self, card: Card) -> None:
        self.repo.remove(self.user_id, card.id)
        after = self.get(card.id)
        if after and not after.hidden:
            raise VerificationError("dossier not removed")
