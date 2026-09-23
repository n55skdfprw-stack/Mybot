"""Бизнес-логика раздела «Ваши дела»."""

from datetime import date, datetime
from typing import Optional

from ..database.repositories import Task, TaskRepository
from . import search


class DuplicateError(Exception):
    pass


class VerificationError(Exception):
    """Операция не подтвердилась при проверке — Альфред не должен говорить «Готово»."""


class TaskService:
    def __init__(self, repo: TaskRepository, user_id: int):
        self.repo = repo
        self.user_id = user_id

    def create(self, title: str, due: Optional[date], force: bool = False) -> Task:
        title = title.strip()
        if not force:
            for t in self.repo.active(self.user_id):
                if search.normalize(t.title) == search.normalize(title) and t.due_date == due:
                    raise DuplicateError(t.title)
        task_id = self.repo.add(self.user_id, title, due)
        task = self.repo.get(self.user_id, task_id)
        if not task:
            raise VerificationError("task not saved")
        return task

    def active(self) -> list[Task]:
        return self.repo.active(self.user_id)

    def relevant(self, today: date) -> list[Task]:
        """Актуальные дела: без даты, на сегодня или просроченные."""
        return [t for t in self.active() if t.due_date is None or t.due_date <= today]

    def done_today(self, today: date) -> list[Task]:
        return self.repo.completed_since(self.user_id, today.isoformat())

    def find(self, query: str) -> list[Task]:
        return search.best_matches(query, self.active(), lambda t: t.title)

    def get(self, task_id: int) -> Optional[Task]:
        return self.repo.get(self.user_id, task_id)

    def update(self, task: Task, new_title: Optional[str], new_due: Optional[date], clear_due: bool) -> Task:
        title = new_title.strip() if new_title else task.title
        due = None if clear_due else (new_due if new_due else task.due_date)
        self.repo.update(self.user_id, task.id, title, due)
        updated = self.repo.get(self.user_id, task.id)
        if not updated or updated.title != title or updated.due_date != due:
            raise VerificationError("task not updated")
        return updated

    def complete(self, tasks: list[Task], now: datetime) -> None:
        self.repo.complete(self.user_id, [t.id for t in tasks], now.isoformat(timespec="seconds"))
        for t in tasks:
            check = self.repo.get(self.user_id, t.id)
            if not check or not check.completed:
                raise VerificationError("task not completed")

    def delete(self, tasks: list[Task]) -> None:
        self.repo.delete(self.user_id, [t.id for t in tasks])
        for t in tasks:
            if self.repo.get(self.user_id, t.id):
                raise VerificationError("task not deleted")
