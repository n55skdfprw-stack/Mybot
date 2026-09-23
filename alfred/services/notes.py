"""Бизнес-логика раздела «Ваши заметки»."""

import os
import re
from typing import Optional

from ..database.repositories import Note, NoteRepository
from . import search
from .tasks import VerificationError


def make_title(content: str) -> str:
    words = content.strip().split()
    title = " ".join(words[:6])
    return title if len(words) <= 6 else title + "…"


_WORD = re.compile(r"[\wё-]+", re.IGNORECASE)


def _same_stem(a: str, b: str) -> bool:
    """«верхнем» и «верхний», «ящике» и «ящик» — одно слово в разных формах."""
    a, b = search.normalize(a), search.normalize(b)
    if min(len(a), len(b)) <= 3:
        return a == b
    common = len(os.path.commonprefix([a, b]))
    return common >= max(3, min(len(a), len(b)) - 3)


def _adapt(orig: str, frm: str, to: str) -> str:
    """Ставит новое слово в ту же форму, что и заменяемое: «нижний» → «нижнем»."""
    if _same_stem(to, frm):
        return orig
    o, f, t = search.normalize(orig), search.normalize(frm), search.normalize(to)
    p = len(os.path.commonprefix([o, f]))
    from_end, orig_end = f[p:], o[p:]
    if from_end and t.endswith(from_end):
        return to[: len(to) - len(from_end)] + orig_end
    if not from_end:
        return to + orig_end
    return to


def fuzzy_replace(content: str, old: str, new: str) -> Optional[str]:
    """Замена с учётом окончаний. None, если фрагмент не найден."""
    idx = search.normalize(content).find(search.normalize(old))
    if idx >= 0:
        result = content[:idx] + new + content[idx + len(old):]
        return " ".join(result.split()).replace(" ,", ",").replace(" .", ".")

    words = list(_WORD.finditer(content))
    old_words = _WORD.findall(old)
    new_words = _WORD.findall(new)
    n = len(old_words)
    if n == 0:
        return None
    for i in range(len(words) - n + 1):
        span = words[i:i + n]
        if all(_same_stem(span[k].group(), old_words[k]) for k in range(n)):
            if len(new_words) == n:
                replacement = " ".join(_adapt(span[k].group(), old_words[k], new_words[k]) for k in range(n))
            else:
                replacement = new
            if span[0].group()[:1].isupper() and replacement:
                replacement = replacement[:1].upper() + replacement[1:]
            result = content[:span[0].start()] + replacement + content[span[-1].end():]
            return " ".join(result.split()).replace(" ,", ",").replace(" .", ".")
    return None


class NoteService:
    def __init__(self, repo: NoteRepository, user_id: int):
        self.repo = repo
        self.user_id = user_id

    def create(self, content: str, title: Optional[str] = None) -> Note:
        content = content.strip()
        title = (title or "").strip() or make_title(content)
        note_id = self.repo.add(self.user_id, title, content)
        note = self.repo.get(self.user_id, note_id)
        if not note:
            raise VerificationError("note not saved")
        return note

    def all(self) -> list[Note]:
        return self.repo.all(self.user_id)

    def get(self, note_id: int) -> Optional[Note]:
        return self.repo.get(self.user_id, note_id)

    def find(self, query: str) -> list[Note]:
        return search.best_matches(query, self.all(), lambda n: f"{n.title} {n.content}")

    def replace_text(self, note: Note, old: str, new: str) -> Optional[Note]:
        """Меняет фрагмент текста. Возвращает None, если фрагмент не найден."""
        content = fuzzy_replace(note.content, old, new)
        if content is None or not content.strip():
            return None
        return self.set_content(note, content)

    def set_content(self, note: Note, content: str) -> Note:
        content = content.strip()
        content = content[:1].upper() + content[1:]
        title = make_title(content)
        self.repo.update(self.user_id, note.id, title, content)
        updated = self.repo.get(self.user_id, note.id)
        if not updated or updated.content != content:
            raise VerificationError("note not updated")
        return updated

    def delete(self, notes: list[Note]) -> None:
        self.repo.delete(self.user_id, [n.id for n in notes])
        for n in notes:
            if self.repo.get(self.user_id, n.id):
                raise VerificationError("note not deleted")
