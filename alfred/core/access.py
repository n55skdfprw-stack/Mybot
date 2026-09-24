"""Режимы доступа: 👑 владелец и 👤 приглашённые пользователи.

У каждого свой Альфред со своими данными. Владелец не видит чужих данных — только список и статистику.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..brain.brain import Brain
from ..database.db import Database
from ..database.repositories import (Account, ContextRepository, NoteRepository, TaskRepository, UserRepository)
from ..database.schedule_repo import EventRepository, NotificationRepository, RuleRepository
from ..services.currency import CurrencyService
from ..services.notes import NoteService
from ..services.schedule import ScheduleService
from ..services.tasks import TaskService
from ..services.weather import WeatherService
from .alfred import Alfred

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 30   # запросов к ИИ в день для приглашённого; кнопки меню не считаются


@dataclass
class Visitor:
    account: Optional[Account]
    kind: str            # owner / user / new (только что принял приглашение) / blocked / stranger


class Access:
    def __init__(self, db: Database, brain: Brain, owner_telegram_id: int, tz: ZoneInfo, city: str,
                 currency: Optional[CurrencyService] = None, weather: Optional[WeatherService] = None,
                 default_limit: int = DEFAULT_LIMIT, clock: Optional[Callable[[], datetime]] = None):
        self.db = db
        self.brain = brain
        self.owner_tg = owner_telegram_id
        self.tz = tz
        self.city = city
        self.currency = currency or CurrencyService()
        self.weather = weather or WeatherService()
        self.default_limit = default_limit
        self.clock = clock
        self.users = UserRepository(db)
        self.started = datetime.now(tz)
        self._alfreds: dict[int, Alfred] = {}

    # ------------------------------------------------------------ владелец
    def setup_owner(self) -> Account:
        acc = self.users.by_telegram(self.owner_tg)
        if acc is None:
            uid = self.users.create(self.owner_tg, None, None, "owner", self.tz.key, self.city)
        else:
            uid = acc.id
            self.users.set_role(uid, "owner")
        return self.users.by_id(uid)

    @property
    def owner(self) -> Account:
        return self.users.by_telegram(self.owner_tg) or self.setup_owner()

    # ------------------------------------------------------------ кто пишет
    def who(self, telegram_id: int, username: Optional[str], name: Optional[str]) -> Visitor:
        if telegram_id == self.owner_tg:
            acc = self.owner
            self.users.touch(acc.id, username, name)
            return Visitor(self.users.by_id(acc.id), "owner")
        acc = self.users.by_telegram(telegram_id)
        if acc:
            self.users.touch(acc.id, username, name)
            return Visitor(self.users.by_id(acc.id), "blocked" if acc.status == "blocked" else "user")
        if self.users.take_invite(username):
            uid = self.users.create(telegram_id, username, name, "user", self.tz.key, self.city)
            log.info("Новый пользователь по приглашению: @%s", username)
            return Visitor(self.users.by_id(uid), "new")
        return Visitor(None, "stranger")

    # ------------------------------------------------------------ Альфред для каждого
    def alfred_for(self, acc: Account) -> Alfred:
        a = self._alfreds.get(acc.id)
        if a is None:
            db, uid = self.db, acc.id
            a = Alfred(
                brain=self.brain,
                tasks=TaskService(TaskRepository(db), uid),
                notes=NoteService(NoteRepository(db), uid),
                schedule=ScheduleService(EventRepository(db), RuleRepository(db), NotificationRepository(db), uid),
                context=ContextRepository(db),
                user_id=uid,
                tz=self.tz,
                clock=self.clock,
                currency=self.currency,
                weather=self.weather,
            )
            a.quota = self._quota(acc.id, a)
            self._alfreds[acc.id] = a
        return a

    def forget(self, user_id: int) -> None:
        self._alfreds.pop(user_id, None)

    def _quota(self, user_id: int, alfred: Alfred) -> Callable[[], Optional[str]]:
        def check() -> Optional[str]:
            acc = self.users.by_id(user_id)
            day = alfred.today().isoformat()
            if acc and acc.role != "owner" and not acc.unlimited:
                limit = self.limit_of(acc)
                if self.users.usage(user_id, day) >= limit:
                    return (f"🎩 Прошу прощения, Сэр! На сегодня лимит сообщений исчерпан ({limit}).\n\n"
                            "Кнопки меню работают без ограничений, а завтра лимит обновится.")
            self.users.add_usage(user_id, day)   # владельцу тоже считаем — для статистики
            return None
        return check

    def limit_of(self, acc: Account) -> int:
        return acc.ai_limit if acc.ai_limit is not None else self.default_limit

    # ------------------------------------------------------------ пауза и остановка
    @property
    def stopped_for_all(self) -> bool:
        return self.users.setting("stopped_for_all") == "1"

    def stop_for_all(self, stopped: bool) -> None:
        self.users.set_setting("stopped_for_all", "1" if stopped else "0")

    def active(self) -> list[tuple[Account, Alfred]]:
        """Кому сейчас слать сводки и напоминания: не на паузе, доступ открыт, Альфред не остановлен для всех."""
        if self.stopped_for_all:
            return []
        return [(acc, self.alfred_for(acc)) for acc in self.users.accounts()
                if acc.status == "active" and not acc.paused]
