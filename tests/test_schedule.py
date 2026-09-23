"""Тесты второго этапа — «Ваш распорядок». Сегодня — среда, 23 сентября 2026, 11:00."""

import asyncio
from datetime import date, datetime

import pytest

from alfred.brain.brain import Brain
from alfred.brain.dates import parse_repeat, parse_time_range, parse_until
from alfred.core.alfred import Alfred
from alfred.database.db import Database
from alfred.database.repositories import ContextRepository, NoteRepository, TaskRepository, UserRepository
from alfred.database.schedule_repo import EventRepository, NotificationRepository, RuleRepository
from alfred.services.notes import NoteService
from alfred.services.schedule import ScheduleService
from alfred.services.tasks import TaskService

from .test_alfred import TZ, FakeLLM, style_ok

TODAY = date(2026, 9, 23)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 23, 11, 0, tzinfo=TZ)

    def __call__(self):
        return self.now

    def set(self, y, mo, d, h, mi=0):
        self.now = datetime(y, mo, d, h, mi, tzinfo=TZ)


def make(tmp_path):
    db = Database(str(tmp_path / "s.db"))
    db.migrate()
    uid = UserRepository(db).ensure(1, "Europe/Moscow", "Санкт-Петербург")
    llm, clock = FakeLLM(), Clock()
    schedule = ScheduleService(EventRepository(db), RuleRepository(db), NotificationRepository(db), uid)
    a = Alfred(Brain(llm), TaskService(TaskRepository(db), uid), NoteService(NoteRepository(db), uid),
               schedule, ContextRepository(db), uid, TZ, clock=clock)
    return a, llm, clock


def run(coro):
    return asyncio.run(coro)


def pending(a):
    return a.schedule.notifs.pending_until(a.user_id, "2099-01-01T00:00")


# ---------------------------------------------------------------- разбор времени

@pytest.mark.parametrize("text,expected", [
    ("в 18:00", ("18:00", None)), ("в 18", ("18:00", None)), ("в шесть", ("18:00", None)),
    ("в шесть утра", ("06:00", None)), ("в 10", ("10:00", None)), ("с 10 до 12", ("10:00", "12:00")),
    ("10:00–12:00", ("10:00", "12:00")), ("в полседьмого", ("18:30", None)), ("в 7 вечера", ("19:00", None)),
    ("в 06:30", ("06:30", None)), ("в два часа дня", ("14:00", None)), ("на 18:30", ("18:30", None)),
    ("на 11", ("11:00", None)), ("к 9 утра", ("09:00", None)), ("в субботу в 18", ("18:00", None)),
    ("в 304 аудитории в 10", ("10:00", None)),
])
def test_time_parsing(text, expected):
    assert parse_time_range(text) == expected


def test_repeat_and_until_parsing():
    assert parse_repeat("каждый понедельник и четверг", TODAY) == ("weekly", [0, 3])
    assert parse_repeat("по будням", TODAY) == ("weekly", [0, 1, 2, 3, 4])
    assert parse_until("до конца октября", TODAY) == date(2026, 10, 31)
    assert parse_until("на ближайшие две недели", TODAY) == date(2026, 10, 6)


# ---------------------------------------------------------------- создание

def test_create_training_all_known(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="training", event_when="в субботу", time_text="в 18:00",
             focus="ноги")
    r = run(a.handle_text("В субботу в 18:00 тренировка ног"))
    style_ok(r.text)
    assert "18:00 — Тренировка (Ноги)" in r.text and "Суббота, 26 сентября" in r.text
    assert pending(a) == []  # о тренировках отдельно не напоминаем


def test_create_asks_only_missing_time(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="training", event_when="в субботу")
    r = run(a.handle_text("Тренировка в субботу"))
    assert r.text == "🎩 Разумеется, Сэр! Во сколько начинается тренировка?"
    llm.said(intent="ANSWER", answer="в 18:00")
    r = run(a.handle_text("в 18:00"))
    assert "18:00 — Тренировка" in r.text
    assert a.schedule.day(date(2026, 9, 26))[0].start_time == "18:00"


def test_create_asks_only_missing_day(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="training", time_text="в 18:00")
    r = run(a.handle_text("Тренировка в 18:00"))
    assert r.text == "🎩 Разумеется, Сэр! На какой день назначить тренировку?"
    llm.said(intent="ANSWER", answer="в субботу")
    run(a.handle_text("В субботу"))
    assert len(a.schedule.day(date(2026, 9, 26))) == 1


def test_duplicate_event_blocked(tmp_path):
    a, llm, _ = make(tmp_path)
    for _ in range(2):
        llm.said(intent="CREATE_EVENT", event_type="training", event_when="в субботу", time_text="в 18:00")
    run(a.handle_text("Тренировка в субботу в 18:00"))
    r = run(a.handle_text("Добавь тренировку в субботу в 18:00"))
    assert "уже есть" in r.text
    llm.said(intent="CREATE_EVENT", event_type="training", event_when="в субботу", time_text="в 18:00",
             force_duplicate=True)
    run(a.handle_text("Добавь ещё одну тренировку в субботу в 18:00"))
    assert len(a.schedule.day(date(2026, 9, 26))) == 2


# ---------------------------------------------------------------- напоминания о лекциях

def test_lecture_reminders_first_hour_second_half(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="lecture", event_when="завтра", time_text="с 10 до 12",
             location="Аудитория 304", discipline="Спортивная физиология")
    llm.said(intent="CREATE_EVENT", event_type="lecture", event_when="завтра", time_text="в 15:00",
             location="Аудитория 205")
    run(a.handle_text("x")); run(a.handle_text("y"))
    kinds = sorted((n.type, n.scheduled_at) for n in pending(a))
    assert kinds == [("lecture", "2026-09-24T14:30"), ("morning_lecture", "2026-09-24T09:00")]


def test_morning_merged_with_lecture_and_tasks(tmp_path):
    a, llm, clock = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить корм коту")
    run(a.handle_text("Купить корм коту"))
    llm.said(intent="CREATE_EVENT", event_type="lecture", event_when="завтра", time_text="с 10 до 12",
             location="Аудитория 304")
    run(a.handle_text("x"))
    clock.set(2026, 9, 24, 9, 0)
    [(nid, reply)] = a.collect_reminders()
    assert reply.text.startswith("🎩 Доброе утро, Сэр!")
    assert "Через час, в 10:00, у вас начинается лекция!" in reply.text
    assert "🚪 Аудитория 304" in reply.text and "☐ Купить корм коту" in reply.text
    a.mark_reminder(nid, sent=True)
    clock.set(2026, 9, 24, 10, 0)
    assert a.check_message("morning") is None  # вторую сводку в 10:00 не шлём


def test_missed_reminder_not_sent_and_summary_restored(tmp_path):
    a, llm, clock = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить корм коту")
    run(a.handle_text("Купить корм коту"))
    llm.said(intent="CREATE_EVENT", event_type="lecture", event_when="завтра", time_text="в 10:00")
    run(a.handle_text("x"))
    clock.set(2026, 9, 24, 9, 40)  # бот «проснулся» с опозданием на 40 минут
    [(nid, reply)] = a.collect_reminders()
    assert reply is None
    a.mark_reminder(nid, sent=False)
    clock.set(2026, 9, 24, 10, 0)
    assert a.check_message("morning") is not None  # раз напоминание пропало — обычная сводка приходит


def test_doctor_reminder_hour_before(tmp_path):
    a, llm, clock = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00",
             comment="взять паспорт")
    run(a.handle_text("Врач в пятницу в 17:00, взять паспорт"))
    clock.set(2026, 9, 25, 16, 0)
    [(_, reply)] = a.collect_reminders()
    style_ok(reply.text)
    assert "в 17:00, у вас приём у врача!" in reply.text and "💬 взять паспорт" in reply.text


# ---------------------------------------------------------------- изменения

def test_reschedule_keeps_comment_and_moves_reminder(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00",
             comment="взять паспорт")
    run(a.handle_text("x"))
    llm.said(intent="UPDATE_EVENT", target="врач", event_type="doctor", new_time_text="на 18:30")
    r = run(a.handle_text("Перенеси врача на 18:30"))
    style_ok(r.text)
    e = a.schedule.day(date(2026, 9, 25))[0]
    assert e.start_time == "18:30" and e.comment == "взять паспорт"
    assert [n.scheduled_at for n in pending(a)] == ["2026-09-25T17:30"]


def test_correction_last_event(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="training", event_when="в субботу", time_text="в 18")
    run(a.handle_text("Тренировка в субботу в 18"))
    llm.said(intent="UPDATE_EVENT", target="LAST", new_time_text="в 19")
    run(a.handle_text("Нет, в 19"))
    llm.said(intent="UPDATE_EVENT", target="LAST", new_time_text="на 20:00")
    run(a.handle_text("Нет, лучше на 20:00"))
    [e] = a.schedule.day(date(2026, 9, 26))
    assert e.start_time == "20:00"


def test_lecture_move_keeps_duration(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="lecture", event_when="завтра", time_text="с 10 до 12")
    run(a.handle_text("x"))
    llm.said(intent="UPDATE_EVENT", target="лекция", event_type="lecture", new_time_text="на 11")
    run(a.handle_text("Перенеси лекцию на 11"))
    [e] = a.schedule.day(date(2026, 9, 24))
    assert (e.start_time, e.end_time) == ("11:00", "13:00")


def test_comment_negation(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00",
             comment="взять полис, паспорт")
    run(a.handle_text("x"))
    llm.said(intent="UPDATE_EVENT", target="врач", event_type="doctor", comment_remove="паспорт")
    run(a.handle_text("К врачу паспорт уже не нужен"))
    assert a.schedule.day(date(2026, 9, 25))[0].comment == "взять полис"


def test_delete_event_cancels_reminder(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00")
    run(a.handle_text("x"))
    assert len(pending(a)) == 1
    llm.said(intent="DELETE_EVENT", target="врач", event_type="doctor")
    r = run(a.handle_text("Отмени врача"))
    style_ok(r.text)
    assert pending(a) == [] and a.schedule.day(date(2026, 9, 25)) == []


def test_ambiguous_events_ask(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="meeting", event_title="Встреча с Сергеем",
             event_when="завтра", time_text="в 12")
    llm.said(intent="CREATE_EVENT", event_type="meeting", event_title="Встреча с Андреем",
             event_when="в пятницу", time_text="в 12")
    run(a.handle_text("x")); run(a.handle_text("y"))
    llm.said(intent="DELETE_EVENT", target="встреча", event_type="meeting")
    r = run(a.handle_text("Удали встречу"))
    assert r.text.endswith("?") and len(r.buttons) == 3


# ---------------------------------------------------------------- повторения

def _create_series(a, llm):
    llm.said(intent="CREATE_EVENT", event_type="training", time_text="в 18:00",
             repeat_text="каждый понедельник и четверг", until_text="до конца октября")
    return run(a.handle_text("Тренировка каждый понедельник и четверг в 18:00 до конца октября"))


def test_create_series(tmp_path):
    a, llm, _ = make(tmp_path)
    r = _create_series(a, llm)
    style_ok(r.text)
    assert "По понедельникам и четвергам, до 31 октября" in r.text
    events = a.schedule.between(TODAY, date(2026, 12, 31))
    assert {e.date.weekday() for e in events} == {0, 3}
    assert events[0].date == date(2026, 9, 24) and events[-1].date == date(2026, 10, 29)
    assert len(events) == 11


def test_series_change_only_thursdays(tmp_path):
    a, llm, _ = make(tmp_path)
    _create_series(a, llm)
    llm.said(intent="UPDATE_EVENT", target="тренировка", event_type="training", event_when="по четвергам",
             new_time_text="в 19", apply_to="series")
    r = run(a.handle_text("Тренировки по четвергам теперь в 19"))
    style_ok(r.text)
    events = a.schedule.between(TODAY, date(2026, 12, 31))
    assert {e.start_time for e in events if e.date.weekday() == 3} == {"19:00"}
    assert {e.start_time for e in events if e.date.weekday() == 0} == {"18:00"}


def test_single_instance_change_leaves_series(tmp_path):
    a, llm, _ = make(tmp_path)
    _create_series(a, llm)
    llm.said(intent="UPDATE_EVENT", target="тренировка", event_type="training", event_when="в понедельник",
             new_time_text="в 20")
    run(a.handle_text("В понедельник тренировка в 20"))
    events = a.schedule.between(TODAY, date(2026, 12, 31))
    changed = [e for e in events if e.start_time == "20:00"]
    assert [e.date for e in changed] == [date(2026, 9, 28)]


def test_deleted_instance_not_recreated(tmp_path):
    a, llm, clock = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="training", time_text="в 18:00", repeat_text="по понедельникам")
    run(a.handle_text("Тренировка по понедельникам в 18:00"))
    llm.said(intent="DELETE_EVENT", target="тренировка", event_type="training", event_when="в понедельник")
    run(a.handle_text("Отмени тренировку в понедельник"))
    assert a.schedule.day(date(2026, 9, 28)) == []
    clock.set(2026, 9, 24, 3, 0)
    a.extend_schedule()
    assert a.schedule.day(date(2026, 9, 28)) == []  # удалённое вручную не возвращается
    assert len(a.schedule.day(date(2026, 10, 5))) == 1


def test_delete_series_needs_confirmation(tmp_path):
    a, llm, _ = make(tmp_path)
    _create_series(a, llm)
    llm.said(intent="DELETE_EVENT", target="тренировка", event_type="training", event_when="по четвергам",
             apply_to="series")
    r = run(a.handle_text("Тренировок по четвергам больше не будет"))
    assert r.text.endswith("?") and "по четвергам" in r.text
    assert len(a.schedule.between(TODAY, date(2026, 12, 31))) == 11
    a.handle_callback(r.buttons[0][0][1])
    events = a.schedule.between(TODAY, date(2026, 12, 31))
    assert {e.date.weekday() for e in events} == {0}


# ---------------------------------------------------------------- просмотр

def test_views_and_tomorrow_summary(tmp_path):
    a, llm, _ = make(tmp_path)
    r = run(a.handle_text("🕰️ Ваш распорядок"))
    assert "ничего нет" in r.text and r.buttons
    llm.said(intent="CREATE_EVENT", event_type="lecture", event_when="завтра", time_text="с 10 до 12",
             location="Аудитория 304", discipline="Спортивная физиология")
    llm.said(intent="CREATE_EVENT", event_type="training", event_when="завтра", time_text="в 18",
             focus="Ноги")
    run(a.handle_text("x")); run(a.handle_text("y"))
    r = a.handle_callback("sched:tomorrow")
    assert r.edit and "10:00–12:00 — Лекция\n📚 Спортивная физиология\n🚪 Аудитория 304" in r.text
    r = a.handle_callback("sched:week")
    assert "🗓️ Завтра, 24 сентября" in r.text and "на этой неделе" in r.text
    s = a.tomorrow_summary()
    assert s.text.startswith("🎩 Добрый вечер, Сэр!\n\nНапоминаю о завтрашнем расписании!")
    assert "🕙 10:00–12:00 — Лекция" in s.text and "🕕 18:00 — Тренировка (Ноги)" in s.text
    assert s.text.endswith("Доброй ночи, Сэр!")


def test_tomorrow_summary_empty(tmp_path):
    a, _, _ = make(tmp_path)
    assert a.tomorrow_summary().text == "🎩 Добрый вечер, Сэр!\n\nНа завтра в расписании ничего не запланировано!"


def test_show_schedule_by_words(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="SHOW_SCHEDULE", event_when="на неделю")
    r = run(a.handle_text("Что у меня на неделе?"))
    style_ok(r.text)


# ---------------------------------------------------------------- живые ошибки из Telegram (23.09, 19:19–19:23)

def test_comment_taken_from_message_when_ai_missed(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00")
    r = run(a.handle_text("Врач в пятницу в 17:00 надо взять паспорт"))
    assert "💬 взять паспорт" in r.text
    assert a.schedule.day(date(2026, 9, 25))[0].comment == "взять паспорт"


def test_event_phrase_does_not_damage_note(tmp_path):
    """ИИ принял «К врачу паспорт уже не нужен» за правку заметки и вырезал «Паспорт»."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="Паспорт в нижнем ящике")
    run(a.handle_text("Запиши, что паспорт в нижнем ящике"))
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00")
    run(a.handle_text("Врач в пятницу в 17:00 надо взять паспорт"))
    llm.said(intent="UPDATE_NOTE", target="паспорт", replace_from="Паспорт", replace_to="")
    r = run(a.handle_text("К врачу паспорт уже не нужен"))
    style_ok(r.text)
    assert a.notes.all()[0].content == "Паспорт в нижнем ящике"
    assert not a.schedule.day(date(2026, 9, 25))[0].comment


def test_series_thursdays_with_extra_single_training(tmp_path):
    """Серия пн/чт + отдельная тренировка ног на 24-е: «по четвергам теперь в 19» не должно спрашивать."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="training", event_when="завтра", time_text="в 19",
             focus="Ноги")
    run(a.handle_text("Завтра в 19 тренировка ног"))
    _create_series(a, llm)
    llm.said(intent="UPDATE_EVENT", target="тренировка", event_type="training", event_when="по четвергам",
             new_time_text="в 19")  # ИИ даже не отметил, что это серия
    r = run(a.handle_text("Тренировки по четвергам теперь в 19"))
    assert not r.buttons and "Изменил все будущие тренировки" in r.text
    events = a.schedule.between(TODAY, date(2026, 12, 31))
    series = [e for e in events if e.recurrence_id]
    assert {e.start_time for e in series if e.date.weekday() == 3} == {"19:00"}
    assert {e.start_time for e in series if e.date.weekday() == 0} == {"18:00"}
    legs = [e for e in events if e.focus == "Ноги"]
    assert len(legs) == 1 and legs[0].recurrence_id is None


def test_week_view_compact(tmp_path):
    a, llm, _ = make(tmp_path)
    for when, t in (("завтра", "в 18:00"), ("завтра", "в 19:00")):
        llm.said(intent="CREATE_EVENT", event_type="training", event_when=when, time_text=t)
        run(a.handle_text("x"))
    r = a.handle_callback("sched:week")
    assert "🗓️ Завтра, 24 сентября\n18:00 — Тренировка\n19:00 — Тренировка" in r.text
    assert "📅" not in r.text and all("📅" not in b[0] for row in r.buttons for b in row)


def test_week_is_until_sunday_and_month_until_month_end(tmp_path):
    a, llm, _ = make(tmp_path)
    _create_series(a, llm)  # пн и чт до конца октября
    week = a.handle_callback("sched:week").text
    assert "Завтра, 24 сентября" in week and "28 сентября" not in week  # неделя — до воскресенья 27-го
    month = a.handle_callback("sched:month").text
    assert "Понедельник, 28 сентября" in month and "октябр" not in month
    buttons = [b[0] for row in a.handle_callback("sched:month").buttons for b in row]
    assert buttons == ["🗓️ Сегодня", "🗓️ Завтра", "🗓️ На этой неделе", "🗓️ В этом месяце"]


def test_cancel_one_monday_keeps_other_mondays(tmp_path):
    """Проверка опасения: «Отмени тренировку в понедельник» не должно удалять все понедельники."""
    a, llm, _ = make(tmp_path)
    _create_series(a, llm)
    llm.said(intent="DELETE_EVENT", target="тренировка", event_type="training", event_when="в понедельник")
    r = run(a.handle_text("Отмени тренировку в понедельник"))
    assert not r.buttons  # без подтверждения — значит, удалено одно занятие
    mondays = [e.date for e in a.schedule.between(TODAY, date(2026, 12, 31)) if e.date.weekday() == 0]
    assert date(2026, 9, 28) not in mondays and date(2026, 10, 5) in mondays and len(mondays) == 4


def test_month_view_by_words(tmp_path):
    a, llm, _ = make(tmp_path)
    _create_series(a, llm)
    llm.said(intent="SHOW_SCHEDULE", event_when="в этом месяце")
    r = run(a.handle_text("Что у меня в этом месяце?"))
    assert "в этом месяце" in r.text


def test_week_is_monday_to_sunday(tmp_path):
    a, llm, clock = make(tmp_path)
    a.schedule.create({"type": "training", "date": "2026-09-21", "start_time": "18:00"}, clock())  # прошедший пн
    a.schedule.create({"type": "training", "date": "2026-09-27", "start_time": "12:00"}, clock())  # вс
    a.schedule.create({"type": "training", "date": "2026-09-28", "start_time": "18:00"}, clock())  # следующий пн
    week = a.handle_callback("sched:week").text
    assert "Понедельник, 21 сентября" in week and "Воскресенье, 27 сентября" in week
    assert "28 сентября" not in week


def test_month_is_whole_month(tmp_path):
    a, llm, clock = make(tmp_path)
    a.schedule.create({"type": "training", "date": "2026-09-01", "start_time": "18:00"}, clock())  # прошло
    a.schedule.create({"type": "training", "date": "2026-09-30", "start_time": "18:00"}, clock())
    a.schedule.create({"type": "training", "date": "2026-10-01", "start_time": "18:00"}, clock())
    month = a.handle_callback("sched:month").text
    assert "1 сентября" in month and "30 сентября" in month and "октября" not in month


def test_comment_remove_picks_event_with_that_comment(tmp_path):
    """Два врача в пятницу, паспорт только у одного — уточнять не нужно."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 18:30")
    run(a.handle_text("Врач в пятницу в 18:30"))
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00")
    run(a.handle_text("Врач в пятницу в 17 надо взять паспорт"))
    llm.said(intent="UPDATE_EVENT", target="врач", event_type="doctor", comment_remove="паспорт")
    r = run(a.handle_text("К врачу паспорт уже не нужен"))
    assert not r.buttons and "Обновил" in r.text
    assert all(not e.comment for e in a.schedule.day(date(2026, 9, 25)))


def test_day_view_compact(tmp_path):
    a, llm, _ = make(tmp_path)
    for t in ("в 18:00", "в 19:00"):
        llm.said(intent="CREATE_EVENT", event_type="training", event_when="завтра", time_text=t)
        run(a.handle_text("x"))
    r = a.handle_callback("sched:tomorrow")
    assert "18:00 — Тренировка\n19:00 — Тренировка" in r.text


def _two_doctors(a, llm):
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 18:30")
    run(a.handle_text("Врач в пятницу в 18:30"))
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00")
    run(a.handle_text("Врач в пятницу в 17 надо взять паспорт"))


def test_delete_by_time_in_message(tmp_path):
    """Живая ошибка: «Удали врача 18:30» спросило «какое событие?»."""
    a, llm, _ = make(tmp_path)
    _two_doctors(a, llm)
    llm.said(intent="DELETE_EVENT", target="врач", event_type="doctor")  # ИИ не выделил время
    r = run(a.handle_text("Удали врача 18:30"))
    assert not r.buttons and "18:30" in r.text
    assert [e.start_time for e in a.schedule.day(date(2026, 9, 25))] == ["17:00"]


def test_comment_words_pick_event_even_if_ai_gave_no_comment_field(tmp_path):
    a, llm, _ = make(tmp_path)
    _two_doctors(a, llm)
    llm.said(intent="UPDATE_EVENT", target="врач", event_type="doctor", comment="паспорт не нужен")
    r = run(a.handle_text("К врачу паспорт уже не нужен"))
    assert not r.buttons  # выбрано событие, где упоминается паспорт
    assert all(not e.comment for e in a.schedule.day(date(2026, 9, 25)))  # не дописал «паспорт не нужен»


def test_note_rewrite_after_colon(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="в нижнем ящике")
    run(a.handle_text("x"))
    llm.said(intent="UPDATE_NOTE", target="ящик", replace_from="Паспорт", replace_to="")
    run(a.handle_text("В заметке про ящик напиши: паспорт в нижнем ящике"))
    assert a.notes.all()[0].content == "Паспорт в нижнем ящике"


def test_negation_when_ai_passed_note_text(tmp_path):
    """Живая ошибка 24.09, 00:24: ИИ сказал «правка заметки» с текстом из заметки про ящик."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="Паспорт лежит в нижнем ящике")
    run(a.handle_text("Запиши что паспорт лежит в нижнем ящике"))
    llm.said(intent="CREATE_EVENT", event_type="doctor", event_when="в пятницу", time_text="в 17:00")
    run(a.handle_text("Врач в пятницу в 17 надо взять паспорт"))
    llm.said(intent="UPDATE_NOTE", target="паспорт", replace_from="лежит в нижнем ящике", replace_to="")
    r = run(a.handle_text("К врачу паспорт уже не нужен"))
    assert "Обновил распорядок" in r.text
    assert not a.schedule.day(date(2026, 9, 25))[0].comment
    assert a.notes.all()[0].content == "Паспорт лежит в нижнем ящике"
