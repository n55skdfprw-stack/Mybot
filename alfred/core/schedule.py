"""Распорядок в ядре Альфреда. Подключается к классу Alfred как дополнение (mixin)."""

import calendar
import logging
import re
from datetime import date, datetime, timedelta
from dataclasses import replace
from typing import Optional

from ..brain.dates import find_dates, parse_date, parse_repeat, parse_time_range, parse_until
from ..brain.parser import BrainResult
from ..database.schedule_repo import Event
from ..services import search
from ..services.notes import fuzzy_replace
from ..services.schedule import ScheduleService, event_dt
from ..services.tasks import DuplicateError
from ..ui import schedule_texts as S
from ..ui import texts as T
from .reply import Reply

log = logging.getLogger(__name__)

UNTIL_RE = re.compile(
    r"\bдо\s+конца\s+\w+|\bдо\s+\d{1,2}(?:[./]\d{1,2}(?:[./]\d{2,4})?|\s+[а-яё]+)"
    r"|\bна\s+(?:ближайшие\s+|следующие\s+)?(?:\d+\s+|[а-яё]+\s+)?(?:недел\w*|месяц\w*)",
    re.IGNORECASE,
)
REPEAT_WORDS = re.compile(r"кажд|\bпо\s+\w+(ам|ям)\b|будн|выходн|ежедн|еженед|ежемес", re.IGNORECASE)
TYPE_WORDS = [("training", r"тренир"), ("lecture", r"лекци"), ("practice", r"практик|семинар|\bпар[аыу]\b"),
              ("doctor", r"врач|стоматолог|терапевт|при[её]м|клиник|анализ"), ("meeting", r"встреч")]
SCHEDULE_BUTTONS = [[("🗓️ Сегодня", "sched:today"), ("🗓️ Завтра", "sched:tomorrow")],
                    [("🗓️ На этой неделе", "sched:week"), ("🗓️ В этом месяце", "sched:month")]]
TELEGRAM_LIMIT = 3800


VERB_RE = re.compile(r"взять|захватить|принести|оплатить|подготовить|распечатать|сдать", re.IGNORECASE)
COMMENT_RE = re.compile(
    r"(?:,|\s)\s*(?:и\s+)?(?:не\s+забыть|надо|нужно|не\s+забудь)?\s*"
    r"((?:взять|захватить|принести|оплатить|подготовить|распечатать|сдать)\b.+)$",
    re.IGNORECASE,
)


def comment_from_message(text: str) -> Optional[str]:
    m = COMMENT_RE.search(text or "")
    return m.group(1).strip(" .!") if m else None


def infer_type(*texts: Optional[str]) -> Optional[str]:
    for text in texts:
        if not text:
            continue
        low = text.casefold()
        for etype, pattern in TYPE_WORDS:
            if re.search(pattern, low):
                return etype
    return None


class ScheduleMixin:
    schedule: ScheduleService

    # ------------------------------------------------------------ просмотр
    def schedule_day_view(self, d: date, edit: bool = False) -> Reply:
        today = self.today()
        events = self.schedule.day(d)
        delta = (d - today).days
        when = {0: "на сегодня", 1: "на завтра"}.get(delta, f"на {S.day_title(d, today).lower()}")
        if not events:
            return Reply(f"🎩 {when.capitalize()} в распорядке ничего нет, Сэр!", buttons=SCHEDULE_BUTTONS, edit=edit)
        body = "\n".join(S.block(e) for e in events)
        return Reply(f"🎩 Ваш распорядок {when}, Сэр!\n\n{body}", buttons=SCHEDULE_BUTTONS, edit=edit)

    def _period_view(self, start: date, end: date, title: str, empty: str, edit: bool) -> Reply:
        today = self.today()
        events = self.schedule.between(start, end)
        if not events:
            return Reply(empty, buttons=SCHEDULE_BUTTONS, edit=edit)
        days: list[str] = []
        current = None
        for e in events:
            if e.date != current:
                current = e.date
                days.append(f"🗓️ {S.day_title(e.date, today)}")
            days[-1] += "\n" + S.block(e)
        text = f"🎩 {title}\n\n"
        shown = 0
        for day in days:
            if len(text) + len(day) > TELEGRAM_LIMIT:
                break
            text += day + "\n\n"
            shown += day.count("\n")
        hidden = len(events) - shown
        if hidden > 0:
            text += f"…и ещё событий: {hidden}!"
        return Reply(text.rstrip(), buttons=SCHEDULE_BUTTONS, edit=edit)

    def schedule_week_view(self, edit: bool = False) -> Reply:
        """Вся текущая неделя: с понедельника по воскресенье."""
        today = self.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return self._period_view(monday, sunday, "Ваш распорядок на этой неделе, Сэр!",
                                 "🎩 На этой неделе в распорядке ничего нет, Сэр!", edit)

    def schedule_month_view(self, edit: bool = False) -> Reply:
        """Весь текущий месяц: с 1-го числа по последнее."""
        today = self.today()
        first = today.replace(day=1)
        last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return self._period_view(first, last, "Ваш распорядок в этом месяце, Сэр!",
                                 "🎩 В этом месяце в распорядке ничего нет, Сэр!", edit)

    def _show_schedule(self, r: BrainResult) -> Reply:
        when = (r.event_when or "").casefold() + " " + (self._message or "").casefold()
        if "месяц" in when:
            return self.schedule_month_view()
        if "недел" in when:
            return self.schedule_week_view()
        d = parse_date(r.event_when, self.today()) or self.today()
        return self.schedule_day_view(d)

    # ------------------------------------------------------------ помощники
    def _event_date(self, r: BrainResult) -> Optional[date]:
        d = parse_date(r.event_when, self.today())
        if d:
            return d
        found = find_dates(self._message or "", self.today())
        return found[0] if len(found) == 1 else None

    def _repeat_source(self, r: BrainResult) -> Optional[str]:
        for text in (r.repeat_text, r.event_when, self._message):
            if text and REPEAT_WORDS.search(text):
                return text
        return None

    def _event_type(self, r: BrainResult) -> str:
        return r.event_type or infer_type(r.target, self._message) or "other"

    # ------------------------------------------------------------ создание
    def _create_event(self, r: BrainResult) -> Reply:
        etype = self._event_type(r)
        start, end = parse_time_range(r.time_text)
        if not start and not r.time_text:
            start, end = parse_time_range(self._message)
        if not start:
            return self._ask(r, "time_text", f"🎩 Разумеется, Сэр! Во сколько начинается {S.TYPE_NOM[etype]}?")

        values = {"type": etype, "title": r.event_title, "start_time": start, "end_time": end,
                  "comment": r.comment or comment_from_message(self._message), "location": r.location, "discipline": r.discipline,
                  "focus": r.focus[:1].upper() + r.focus[1:] if r.focus else None}

        repeat_text = self._repeat_source(r)
        if repeat_text:
            first = self._event_date(r) if r.event_when and not REPEAT_WORDS.search(r.event_when) else None
            start_date = first or self.today()
            kind, days = parse_repeat(repeat_text, start_date)
            if kind:
                until_text = r.until_text
                if not until_text:
                    m = UNTIL_RE.search(self._message or "")  # «до конца октября», если ИИ не выделил
                    until_text = m.group(0) if m else None
                end_date = parse_until(until_text, self.today())
                rule_values = {**values, "kind": kind, "weekdays": ",".join(map(str, days)),
                               "month_day": start_date.day if kind == "monthly" else None,
                               "start_date": start_date.isoformat(),
                               "end_date": end_date.isoformat() if end_date else None}
                rule, created = self.schedule.create_series(rule_values, self.now())
                if created:
                    self._set_last("event", created[0].id)
                desc = S.repeat_description(rule.kind, rule.weekdays, rule.month_day)
                until = f"до {T.human_date(rule.end_date)}" if rule.end_date else "без даты окончания"
                sample = created[0] if created else None
                body = S.block(sample) if sample else f"{start} — {S.TYPE_LABEL[etype]}"
                return Reply(f"{T.pick('🎩 Разумеется, Сэр! Записал в распорядок!', '🎩 Записал, Сэр!')}"
                             f"\n\n🔁 {desc}, {until}\n{body}")

        d = self._event_date(r)
        if not d:
            return self._ask(r, "event_when", f"🎩 Разумеется, Сэр! На какой день назначить {S.TYPE_ACC[etype]}?")
        values["date"] = d.isoformat()
        try:
            event = self.schedule.create(values, self.now(), force=r.force_duplicate)
        except DuplicateError:
            return Reply(f"🎩 Сэр, такая {S.TYPE_NOM[etype]} уже есть в вашем распорядке!"
                         if etype in ("lecture", "practice", "training", "meeting")
                         else f"🎩 Сэр, такое событие уже есть в вашем распорядке!")
        self._set_last("event", event.id)
        head = T.pick("🎩 Разумеется, Сэр! Записал в распорядок!", "🎩 Записал, Сэр!")
        return Reply(f"{head}\n\n🗓️ {S.day_title(event.date, self.today())}\n{S.block(event)}")

    # ------------------------------------------------------------ поиск существующего
    def _resolve_events(self, r: BrainResult) -> list[Event]:
        ctx = self._ctx()
        last = self.schedule.get(ctx.entity_id) if ctx.entity_type == "event" and ctx.entity_id else None
        # Тип по словам пользователя важнее догадки ИИ: «тренировки» — это тренировки, даже если ИИ сказал «другое».
        said = infer_type(r.target, self._message)
        if r.target == "LAST" or (not r.target and not r.event_type and not r.event_when and not said):
            if last:
                return [last]
        etype = said or (r.event_type if r.event_type != "other" else None)
        on = None if (r.event_when and REPEAT_WORDS.search(r.event_when)) else parse_date(r.event_when, self.today())
        at = parse_time_range(r.time_text)[0] if r.time_text else None
        if not at and r.intent == "DELETE_EVENT":
            at = parse_time_range(self._message)[0]  # «Удали врача 18:30» — время есть в самом сообщении
        words = r.target if r.target and r.target != "LAST" and not infer_type(r.target) else None
        found = self.schedule.find(self.today(), words, etype, on, at)
        if not found and at:
            found = self.schedule.find(self.today(), words, etype, on, None)
        if not found and words and etype:
            found = self.schedule.find(self.today(), None, etype, on, None)
        if not found and on and etype and REPEAT_WORDS.search(self._message or ""):
            found = self.schedule.find(self.today(), None, etype, None, None)
        if r.comment_remove and len(found) > 1:
            # «К врачу паспорт уже не нужен» — берём то событие, где паспорт действительно есть.
            with_it = [e for e in found if e.comment and fuzzy_replace(e.comment, r.comment_remove, "") is not None]
            if with_it:
                found = with_it
        weekday = self._only_weekday(r) if (r.apply_to == "series" or not on) else None
        if weekday is not None:
            found = [e for e in found if e.date.weekday() == weekday] or found
        if r.apply_to == "series":
            recurring = [e for e in found if e.recurrence_id]
            if recurring:
                found = recurring
        return found

    def _infer_series(self, r: BrainResult) -> BrainResult:
        """«Тренировки по четвергам теперь в 19», «все тренировки» — это вся серия, даже если ИИ не отметил."""
        if r.apply_to == "series":
            return r
        text = self._message or ""
        if (r.event_when and REPEAT_WORDS.search(r.event_when)) or re.search(
                r"\bтеперь\b|\bвсе\b|\bвсегда\b|\bкажд|\bпо\s+\w+(ам|ям)\b", text, re.IGNORECASE):
            return replace(r, apply_to="series")
        return r

    def _only_weekday(self, r: BrainResult) -> Optional[int]:
        if not r.event_when:
            return None
        kind, days = parse_repeat(r.event_when, self.today())
        if kind == "weekly" and len(days) == 1:
            return days[0]
        d = parse_date(r.event_when, self.today())
        return d.weekday() if d else None

    def _pick_event(self, r: BrainResult, found: list[Event]) -> Optional[Event] | Reply:
        if not found:
            return Reply("🎩 Сэр, я не нашёл такое событие в распорядке! Уточните, пожалуйста, какое именно?")
        if len(found) == 1:
            return found[0]
        # Слова из сообщения совпадают с комментарием или названием ровно одного события — берём его.
        def text(e: Event) -> str:
            return " ".join(filter(None, [e.title, e.comment, e.discipline, e.location, e.focus]))
        scores = [(search.score(self._message or "", text(e)), e) for e in found]
        best = max(sc for sc, _ in scores)
        leaders = [e for sc, e in scores if sc == best]
        if best > 0 and len(leaders) == 1:
            return leaders[0]
        rules = {e.recurrence_id for e in found}
        if len(rules) == 1 and None not in rules:
            return min(found, key=event_dt)  # одно повторяющееся занятие — берём ближайшее
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка",
                                           "choose": "event"})
        today = self.today()
        rows = [[(S.short_choice(e, today), f"pick:{e.id}")] for e in sorted(found, key=event_dt)[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply("🎩 Сэр, уточните, пожалуйста, какое именно событие?", buttons=rows)

    # ------------------------------------------------------------ изменение
    def _event_changes(self, r: BrainResult, e: Event) -> dict | Reply:
        changes: dict = {}
        negation = re.search(r"\bне\s+(нужн|надо|брать|бери|понадоб)|\bуже\s+не\b|\bбольше\s+не\b",
                             self._message or "", re.IGNORECASE)
        given_missing = bool(r.comment_remove and e.comment
                             and fuzzy_replace(e.comment, r.comment_remove, "") is None)
        if negation and e.comment and (not r.comment_remove or given_missing):
            # «К врачу паспорт уже не нужен» — убираем из комментария то, что упомянуто в сообщении.
            words = [w for w in re.findall(r"[\wё-]+", e.comment)
                     if search.score(w, self._message) > 0 and not VERB_RE.fullmatch(w)]
            if words:
                r = replace(r, comment_remove=" ".join(words), comment=None)
        new_day = parse_date(r.new_event_when, self.today())
        if new_day:
            changes["date"] = new_day.isoformat()
        new_start, new_end = parse_time_range(r.new_time_text)
        if new_start:
            changes["start_time"] = new_start
            if new_end:
                changes["end_time"] = new_end
            elif e.end_time:  # сохраняем длительность
                old_start = datetime.strptime(e.start_time, "%H:%M")
                old_end = datetime.strptime(e.end_time, "%H:%M")
                shifted = datetime.strptime(new_start, "%H:%M") + (old_end - old_start)
                changes["end_time"] = shifted.strftime("%H:%M")
        for field in ("location", "discipline"):
            value = getattr(r, field)
            if value and value != getattr(e, field):
                changes[field] = value
        if r.focus and r.focus != e.focus:
            changes["focus"] = r.focus[:1].upper() + r.focus[1:]
        if r.comment_remove:
            if not e.comment:
                return Reply("🎩 Сэр, у этого события нет комментария!")
            new_comment = fuzzy_replace(e.comment, r.comment_remove, "")
            if new_comment is None:
                return Reply(f"🎩 Сэр, в комментарии нет слов «{r.comment_remove}»! Что именно убрать?")
            new_comment = new_comment.strip(" ,;.")
            if re.fullmatch(r"(?:и\s+)?(?:взять|захватить|принести|оплатить|подготовить|распечатать|сдать)?",
                            new_comment, re.IGNORECASE):
                new_comment = ""  # от «взять паспорт» остался только глагол — убираем целиком
            changes["comment"] = new_comment or None
        elif r.comment and (not e.comment or r.comment.casefold() not in e.comment.casefold()):
            changes["comment"] = f"{e.comment}, {r.comment}" if e.comment else r.comment
        return changes

    def _update_event(self, r: BrainResult, chosen: Optional[Event] = None) -> Reply:
        r = self._infer_series(r) if not chosen else r
        picked = chosen or self._pick_event(r, self._resolve_events(r))
        if isinstance(picked, Reply):
            return picked
        e = picked
        changes = self._event_changes(r, e)
        if isinstance(changes, Reply):
            return changes
        if not changes:
            self._set_last("event", e.id)
            return Reply(f"🎩 Сэр, что именно изменить: «{S.label(e)}», {S.day_title(e.date, self.today()).lower()}?")

        if r.apply_to == "series" and e.recurrence_id:
            rule = self.schedule.rule(e.recurrence_id)
            if "date" in changes:
                return Reply("🎩 Сэр, день у повторяющихся занятий пока меняется так: удалите их и создайте заново!")
            updated = self.schedule.update_series(rule, changes, self.now(), self._only_weekday(r))
            if not updated:
                return Reply("🎩 Сэр, будущих занятий по этому расписанию не нашлось!")
            self._set_last("event", updated[0].id)
            return Reply(f"🎩 Готово, Сэр! Изменил все будущие {S.TYPE_PLURAL[e.type]} ({len(updated)})!"
                         f"\n\n{S.block(updated[0])}")

        updated = self.schedule.update(e, changes, self.now())
        self._set_last("event", updated.id)
        return Reply(f"🎩 Готово, Сэр! Обновил распорядок!\n\n🗓️ {S.day_title(updated.date, self.today())}"
                     f"\n{S.block(updated)}")

    # ------------------------------------------------------------ удаление
    def _delete_event(self, r: BrainResult, chosen: Optional[Event] = None) -> Reply:
        r = self._infer_series(r) if not chosen else r
        if r.apply_to == "series" and not chosen and self._only_weekday(r) is None:
            # «Удали все тренировки» — все расписания этого вида (после «по четвергам теперь…» их может быть два).
            found = self._resolve_events(r)
            etype = found[0].type if found else "other"
            rule_ids = sorted({x.recurrence_id for x in found if x.recurrence_id})
            if len(rule_ids) > 1:
                return Reply(f"🎩 Сэр, вы действительно хотите удалить все будущие {S.TYPE_PLURAL[etype]}?",
                             buttons=[[("🗑 Да, удалить", "confirm:del_rules:" + ",".join(map(str, rule_ids))),
                                       ("↩️ Нет, оставить", "confirm:no")]])
        picked = chosen or self._pick_event(r, self._resolve_events(r))
        if isinstance(picked, Reply):
            return picked
        e = picked
        if r.apply_to == "series" and e.recurrence_id and not chosen:
            weekday = self._only_weekday(r)
            rule = self.schedule.rule(e.recurrence_id)
            what = (f"все {S.TYPE_PLURAL[e.type]} по {S.WEEKDAY_DAT_PL[weekday]}" if weekday is not None
                    and len(rule.weekdays) > 1 else f"все будущие {S.TYPE_PLURAL[e.type]} этого расписания")
            wd = "-" if weekday is None else str(weekday)
            return Reply(f"🎩 Сэр, вы действительно хотите удалить {what}?",
                         buttons=[[("🗑 Да, удалить", f"confirm:del_series:{e.id}:{wd}"),
                                   ("↩️ Нет, оставить", "confirm:no")]])
        self.schedule.delete([e], self.now())
        self._set_last("event", None)
        return Reply(f"🎩 Удалил из распорядка, Сэр!\n\n❌ {S.day_title(e.date, self.today())} — "
                     f"{e.start_time} {S.label(e)}")

    def _confirm_delete_rules(self, ids: str) -> Reply:
        removed = 0
        for rule_id in (int(x) for x in ids.split(",") if x.isdigit()):
            rule = self.schedule.rule(rule_id)
            if rule:
                removed += len(self.schedule.delete_series(rule, self.now()))
        self._set_last("event", None)
        return Reply(f"🎩 Готово, Сэр! Удалил из распорядка: {removed}!", edit=True)

    def _confirm_delete_series(self, event_id: int, wd: str) -> Reply:
        e = self.schedule.get(event_id)
        if not e or not e.recurrence_id:
            return Reply("🎩 Сэр, этих занятий уже нет в распорядке!", edit=True)
        rule = self.schedule.rule(e.recurrence_id)
        removed = self.schedule.delete_series(rule, self.now(), None if wd == "-" else int(wd))
        self._set_last("event", None)
        return Reply(f"🎩 Готово, Сэр! Удалил из распорядка: {len(removed)}!", edit=True)

    # ------------------------------------------------------------ напоминания
    def _reminder_text(self, e: Event, sent_at: datetime) -> str:
        minutes = round((event_dt(e) - sent_at).total_seconds() / 60)
        delta = "час" if minutes == 60 else f"{minutes} минут"
        if e.type in ("lecture", "practice"):
            what = f"у вас начинается {S.TYPE_NOM[e.type]}"
        elif e.type == "doctor":
            what = f"у вас {e.title.lower() if e.title else 'приём у врача'}"
        elif e.type == "meeting":
            what = f"у вас {e.title[:1].lower() + e.title[1:] if e.title else 'встреча'}"
        else:
            what = f"у вас «{S.label(e)}»"
        lines = [f"🎩 {T.day_greeting(sent_at)}, Сэр!", "", f"Через {delta}, в {e.start_time}, {what}!"]
        extra = S.details(e)
        if extra:
            lines += ["", *extra]
        return "\n".join(lines)

    def collect_reminders(self) -> list[tuple[int, Optional[Reply]]]:
        """Уведомления, которые пора отправить. Сильно опоздавшие не отправляются (бот был выключен)."""
        now = self.now().replace(tzinfo=None)
        due = self.schedule.notifs.pending_until(self.user_id, now.strftime("%Y-%m-%dT%H:%M"))
        result = []
        for n in due:
            scheduled = datetime.strptime(n.scheduled_at, "%Y-%m-%dT%H:%M")
            e = self.schedule.get(n.event_id) if n.event_id else None
            if now - scheduled > timedelta(minutes=5) or not e:
                result.append((n.id, None))
                continue
            text = self._reminder_text(e, scheduled)
            buttons = None
            if n.type == "morning_lecture":
                tasks = self.tasks.relevant(self.today())
                if tasks:
                    lines = "\n".join(T.task_line(t, self.today()) for t in tasks)
                    text += f"\n\nВот актуальный список дел!\n\n{lines}"
                    buttons = [[("❌ Не актуально!", "chk:notactual"), ("👍 Спасибо, Альфред!", "chk:thanks")]]
            result.append((n.id, Reply(text, buttons=buttons)))
        return result

    def mark_reminder(self, notif_id: int, sent: bool) -> None:
        if sent:
            self.schedule.notifs.mark_sent(notif_id)
        else:
            self.schedule.notifs.mark_missed(notif_id)

    def tomorrow_summary(self) -> Reply:
        tomorrow = self.today() + timedelta(days=1)
        events = self.schedule.day(tomorrow)
        if not events:
            return Reply("🎩 Добрый вечер, Сэр!\n\nНа завтра в расписании ничего не запланировано!")
        body = "\n\n".join(S.block(e, clock=True) for e in events)
        return Reply(f"🎩 Добрый вечер, Сэр!\n\nНапоминаю о завтрашнем расписании!\n\n{body}\n\nДоброй ночи, Сэр!")

    def extend_schedule(self) -> None:
        self.schedule.extend_all(self.now().replace(tzinfo=None))
