"""Бизнес-логика раздела «Ваши заметки»."""

from typing import Optional

from ..database.repositories import Note, NoteRepository
from . import search
from .tasks import VerificationError


def make_title(content: str) -> str:
    words = content.strip().split()
    title = " ".join(words[:6])
    return title if len(words) <= 6 else title + "…"


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
        idx = search.normalize(note.content).find(search.normalize(old))
        if idx < 0:
            return None
        content = note.content[:idx] + new + note.content[idx + len(old):]
        return self.set_content(note, content)

    def set_content(self, note: Note, content: str) -> Note:
        content = content.strip()
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
