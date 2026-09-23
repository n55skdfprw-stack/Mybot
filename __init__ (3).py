"""Бизнес-логика раздела «Ваш распорядок»."""

import calendar
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Optional

from ..database.schedule_repo import Event, EventRepository, NotificationRepository, Rule, RuleRepository
from . import search
from .tasks import DuplicateError, VerificationError

HORIZON_DAYS = 84          # повторения без даты окончания разворачиваются на 12 недель вперёд
SEARCH_DAYS = 120          # поиск события для изменения — в пределах 4 месяцев
MORNING_LIMIT = time(10, 0)

STUDY_TYPES = {"lecture", "practice"}   # напоминание: первое за день — за час, остальные — за 30 минут
NO_REMINDER = {"training"}             # о тренировках отдельно не напоминаем
EVENT_TYPES = {"lecture", "practice", "training", "doctor", "meeting", "other"}


def event_dt(e: Event) -> datetime:
    h, m = map(int, e.start_time.split(":"))
    return datetime.combine(e.date, time(h, m))


def rule_dates(rule: Rule, start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if rule.kind == "weekly" and d.weekday() in rule.weekdays:
            days.append(d)
        elif rule.kind == "monthly":
            last = calendar.monthrange(d.year, d.month)[1]
            if d.day == min(rule.month_day or 1, last):
                days.append(d)
        d += timedelta(days=1)
    return days


class ScheduleService:
    def __init__(self, events: EventRepository, rules: RuleRepository, notifs: NotificationRepository,
                 user_id: int):
        self.events = events
        self.rules = rules
        self.notifs = notifs
        self.user_id = user_id

    # ------------------------------------------------------------ чтение
    def get(self, event_id: int) -> Optional[Event]:
        return self.events.get(self.user_id, event_id)

    def rule(self, rule_id: int) -> Optional[Rule]:
        return self.rules.get(self.user_id, rule_id)

    def day(self, d: date) -> list[Event]:
        return self.events.between(self.user_id, d, d)

    def between(self, a: date, b: date) -> list[Event]:
        return self.events.between(self.user_id, a, b)

    def find(self, today: date, target: Optional[str], etype: Optional[str], on: Optional[date],
             at: Optional[str]) -> list[Event]:
        """Кандидаты среди будущих событий. Все указанные признаки должны совпасть."""
        pool = self.between(today, today + timedelta(days=SEARCH_DAYS))
        if on:
            pool = [e for e in pool if e.date == on]
        if etype:
            pool = [e for e in pool if e.type == etype]
        if at:
            pool = [e for e in pool if e.start_time == at]
        if target:
            def text(e: Event) -> str:
                return " ".join(filter(None, [e.title, e.discipline, e.focus, e.location, e.comment]))
            by_words = search.best_matches(target, [e for e in pool if text(e)], text)
            if by_words:
                pool = by_words
            elif not etype:
                return []
        return pool

    # ------------------------------------------------------------ создание
    def _is_duplicate(self, values: dict) -> bool:
        for e in self.day(date.fromisoformat(values["date"])):
            if (e.start_time == values["start_time"] and e.type == values["type"]
                    and search.normalize(e.title or "") == search.normalize(values.get("title") or "")):
                return True
        return False

    def create(self, values: dict, now: datetime, force: bool = False) -> Event:
        if not force and self._is_duplicate(values):
            raise DuplicateError(values["type"])
        event_id = self.events.add(self.user_id, values)
        event = self.get(event_id)
        if not event:
            raise VerificationError("event not saved")
        self.sync([event.date], now)
        return event

    def create_series(self, rule_values: dict, now: datetime) -> tuple[Rule, list[Event]]:
        today = now.date()
        start = date.fromisoformat(rule_values["start_date"])
        end = date.fromisoformat(rule_values["end_date"]) if rule_values.get("end_date") else None
        until = min(end, today + timedelta(days=HORIZON_DAYS)) if end else today + timedelta(days=HORIZON_DAYS)
        rule_id = self.rules.add(self.user_id, {**rule_values, "generated_until": until.isoformat()})
        rule = self.rule(rule_id)
        if not rule:
            raise VerificationError("rule not saved")
        created = self._generate(rule, start, until)
        self.sync(sorted({e.date for e in created}), now)
        return rule, created

    def _generate(self, rule: Rule, start: date, end: date) -> list[Event]:
        created = []
        for d in rule_dates(rule, start, end):
            values = {"type": rule.type, "title": rule.title, "date": d.isoformat(),
                      "start_time": rule.start_time, "end_time": rule.end_time, "comment": rule.comment,
                      "location": rule.location, "discipline": rule.discipline, "focus": rule.focus,
                      "recurrence_id": rule.id}
            if self._is_duplicate(values):
                continue
            created.append(self.get(self.events.add(self.user_id, values)))
        return created

    def extend_all(self, now: datetime) -> None:
        """Раз в день дописывает повторения ещё на один день вперёд (новые, а не удалённые вручную)."""
        today = now.date()
        horizon = today + timedelta(days=HORIZON_DAYS)
        for rule in self.rules.active(self.user_id, today):
            last = rule.generated_until or (rule.start_date - timedelta(days=1))
            until = min(rule.end_date, horizon) if rule.end_date else horizon
            if until > last:
                created = self._generate(rule, last + timedelta(days=1), until)
                self.rules.update(self.user_id, rule.id, {"generated_until": until.isoformat()})
                self.sync(sorted({e.date for e in created}), now)

    # ------------------------------------------------------------ изменение
    def update(self, event: Event, changes: dict, now: datetime) -> Event:
        values = {**event.as_values(), **changes}
        self.notifs.cancel_pending_for_events(self.user_id, [event.id])
        self.events.update(self.user_id, event.id, values)
        updated = self.get(event.id)
        if not updated or any(updated.as_values().get(k) != v for k, v in changes.items()):
            raise VerificationError("event not updated")
        self.sync(sorted({event.date, updated.date}), now)
        return updated

    def update_series(self, rule: Rule, changes: dict, now: datetime,
                      only_weekday: Optional[int] = None) -> list[Event]:
        """Меняет правило и все будущие события по нему. Если указан день недели — только его."""
        today = now.date()
        rule_changes = {k: v for k, v in changes.items() if k != "date"}
        if only_weekday is not None and len(rule.weekdays) > 1:
            # Отделяем один день недели в собственное правило.
            rest = [d for d in rule.weekdays if d != only_weekday]
            self.rules.update(self.user_id, rule.id, {"weekdays": ",".join(map(str, rest))})
            new_values = {
                "kind": "weekly", "weekdays": str(only_weekday), "month_day": None, "type": rule.type,
                "title": rule.title, "start_time": rule.start_time, "end_time": rule.end_time,
                "comment": rule.comment, "location": rule.location, "discipline": rule.discipline,
                "focus": rule.focus, "start_date": today.isoformat(),
                "end_date": rule.end_date.isoformat() if rule.end_date else None,
                "generated_until": rule.generated_until.isoformat() if rule.generated_until else None,
                **rule_changes,
            }
            new_id = self.rules.add(self.user_id, new_values)
            targets = [e for e in self.events.of_rule(self.user_id, rule.id, today) if e.date.weekday() == only_weekday]
            reassign = {"recurrence_id": new_id}
        else:
            if rule_changes:
                self.rules.update(self.user_id, rule.id, rule_changes)
            targets = self.events.of_rule(self.user_id, rule.id, today)
            reassign = {}
        updated = []
        self.notifs.cancel_pending_for_events(self.user_id, [e.id for e in targets])
        for e in targets:
            values = {**e.as_values(), **rule_changes, **reassign}
            self.events.update(self.user_id, e.id, values)
            updated.append(self.get(e.id))
        self.sync(sorted({e.date for e in targets}), now)
        return updated

    # ------------------------------------------------------------ удаление
    def delete(self, events: list[Event], now: datetime) -> None:
        ids = [e.id for e in events]
        self.notifs.cancel_pending_for_events(self.user_id, ids)
        self.events.delete(self.user_id, ids)
        for e in events:
            if self.get(e.id):
                raise VerificationError("event not deleted")
        self.sync(sorted({e.date for e in events}), now)

    def delete_series(self, rule: Rule, now: datetime, only_weekday: Optional[int] = None) -> list[Event]:
        today = now.date()
        future = self.events.of_rule(self.user_id, rule.id, today)
        if only_weekday is not None and len(rule.weekdays) > 1:
            future = [e for e in future if e.date.weekday() == only_weekday]
            rest = [d for d in rule.weekdays if d != only_weekday]
            self.rules.update(self.user_id, rule.id, {"weekdays": ",".join(map(str, rest))})
        else:
            self.rules.update(self.user_id, rule.id, {"end_date": (today - timedelta(days=1)).isoformat()})
        self.delete(future, now)
        return future

    # ------------------------------------------------------------ уведомления
    def sync(self, days: list[date], now: datetime) -> None:
        """Пересоздаёт будущие уведомления для указанных дней. Прошедшие не трогает и не повторяет."""
        now_str = now.strftime("%Y-%m-%dT%H:%M")
        for d in days:
            events = self.day(d)
            self.notifs.cancel_pending_for_events(self.user_id, [e.id for e in events])
            study = sorted((e for e in events if e.type in STUDY_TYPES), key=event_dt)
            for idx, e in enumerate(study):
                at = event_dt(e) - timedelta(minutes=60 if idx == 0 else 30)
                ntype = "morning_lecture" if idx == 0 and at.time() <= MORNING_LIMIT else "lecture"
                self._schedule(e, ntype, at, now_str)
            for e in events:
                if e.type not in STUDY_TYPES and e.type not in NO_REMINDER:
                    self._schedule(e, "event", event_dt(e) - timedelta(minutes=60), now_str)

    def _schedule(self, e: Event, ntype: str, at: datetime, now_str: str) -> None:
        at_str = at.strftime("%Y-%m-%dT%H:%M")
        if at_str > now_str:
            self.notifs.add(self.user_id, e.id, ntype, at_str)

    def morning_merged(self, d: date) -> bool:
        """Есть ли сегодня утреннее напоминание о лекции, которое заменяет сводку в 10:00."""
        return any(not n.cancelled for n in self.notifs.for_day(self.user_id, d, "morning_lecture"))
