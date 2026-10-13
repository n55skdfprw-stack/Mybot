"""Проверка «по-взрослому» 13.10.2026: 20 ошибок, найденных обстрелом живыми фразами. Каждая — с исходной фразой,
чтобы не вернулась. Сегодня в тестах — среда, 23 сентября 2026 (как в test_alfred), где не сказано иначе."""

from datetime import date, timedelta

import pytest

from alfred.brain.dates import find_dates, parse_repeat, parse_time_range, parse_until
from alfred.brain.money import parse_amount, parse_period

from .test_alfred import NOW, make, run

TUE = date(2026, 10, 13)                     # вторник — для разбора дат


# ---------------------------------------------------------------- суммы и даты расходов

@pytest.mark.parametrize("phrase,amount", [
    ("Вчера в 18 потратил 300 на такси", 300), ("Заплатил 20 числа 500 за интернет", 500),
    ("Купил 2 пары носков за 400", 400), ("3 кг яблок за 450", 450), ("в 15:00 потратил 200", 200),
    ("2 500", 2500), ("120к", 120000), ("пятьсот", 500), ("сто рублей", 100), ("26 числа", None),
    ("к 9 потратил 150 р", 150), ("не 500, а 600", 600), ("500", 500),
])
def test_amount_is_the_real_sum(phrase, amount):
    assert parse_amount(phrase) == amount


def _expense(tmp_path, phrase, **ai):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EXPENSE", **ai)
    run(a.handle_text(phrase))
    return a.finance.latest()[0]


def test_expense_amount_from_real_phrases(tmp_path):
    op = _expense(tmp_path, "Вчера в 18 потратил 300 на такси", amount_text="300", category="такси", op_when="вчера")
    assert (op.amount, op.date) == (300, date(2026, 9, 22))


def test_expense_on_weekday_is_last_week_not_last_year(tmp_path):
    op = _expense(tmp_path, "В пятницу потратил 500 на такси", amount_text="500", category="такси",
                  op_when="в пятницу")
    assert op.date == date(2026, 9, 18)                           # прошедшая пятница, а не 2025 год


def test_expense_on_day_of_month_is_last_month(tmp_path):
    op = _expense(tmp_path, "25 числа потратил 500 на такси", amount_text="500", category="такси",
                  op_when="25 числа")
    assert op.date == date(2026, 8, 25)                           # 25-е ещё не наступило → прошлый месяц


def test_expense_date_of_ticket_is_not_purchase_date(tmp_path):
    op = _expense(tmp_path, "Купил билет на 20 октября за 3000", amount_text="3000", category="развлечения")
    assert op.date == NOW.date()                                  # «на 20 октября» — не день траты


def test_period_day_before_yesterday():
    assert parse_period("позавчера", date(2026, 9, 23))[:2] == (date(2026, 9, 21), date(2026, 9, 21))


# ---------------------------------------------------------------- медкарта и аллергии

def test_corrected_something_is_not_recovery(tmp_path):
    a, llm, _ = make(tmp_path)
    a.med.add_case("Ангина", a.today(), None, None, [])
    llm.said(intent="UPDATE_EVENT", target="встреча")
    run(a.handle_text("Я поправил время встречи"))
    assert a.med.cases()[0].ended is None                         # болезнь не закрыта
    llm.said(intent="UNKNOWN")
    run(a.handle_text("Я поправился"))
    assert a.med.cases()[0].ended is not None                     # а вот это — выздоровление


def test_someone_elses_allergy_is_not_mine(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="У мамы аллергия на кошек")
    run(a.handle_text("У мамы аллергия на кошек, запиши"))
    assert a.med.allergies() == []
    llm.said(intent="UNKNOWN")
    run(a.handle_text("У меня аллергия на орехи, запиши"))
    assert [x.text for x in a.med.allergies()] == ["Орехи"]       # своя — без хвоста «, запиши»


# ---------------------------------------------------------------- дни рождения, досье, напоминания

def test_birthday_words_in_todo_and_reminder(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить подарок на др Сергею", due_when="к 12 марта")
    run(a.handle_text("Купить подарок на др Сергею к 12 марта"))
    assert [t.title for t in a.tasks.active()] == ["Купить подарок на др Сергею"]
    llm.said(intent="UNKNOWN")
    r = run(a.handle_text("У мамы др 12 марта"))                   # а это — день рождения (ИИ не понял — код поправил)
    assert r.text == "🎩 Разумеется, Сэр! Чей это день рождения?" and a._ctx().intent == "CREATE_BIRTHDAY"


def test_right_understood_is_not_rename(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_DEBT", person="Сергей", direction="owes_me", amount_text="500")
    run(a.handle_text("Сергей должен мне 500"))
    llm.said(intent="THANKS")
    run(a.handle_text("правильно понял"))
    assert [p.first_name for p, _d in a.debts.active()] == ["Сергей"]
    r = run(a.handle_text("Правильно: Серёжа"))
    assert "Исправил имя" in r.text


def test_reminder_is_moved_only_right_after_it(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_REMINDER", title="Позвонить маме", time_text="в 15:00")
    run(a.handle_text("Напомни в 15:00 позвонить маме"))
    r = run(a.handle_text("лучше в 16"))
    assert "Перенёс напоминание" in r.text                        # сразу после — переносим
    a._clock = lambda: NOW + timedelta(hours=2)
    llm.said(intent="CREATE_EVENT", event_type="meeting", event_title="Встреча с Олей", event_when="сегодня",
             time_text="на 18:00")
    r = run(a.handle_text("на 18:00 встреча с Олей сегодня"))
    assert "Перенёс напоминание" not in r.text                    # через 2 часа — это новая запись


# ---------------------------------------------------------------- даты и время

@pytest.mark.parametrize("phrase,want", [
    ("после завтра", date(2026, 10, 15)), ("в пт", date(2026, 10, 16)), ("в сб", date(2026, 10, 17)),
    ("на следующей неделе в среду", date(2026, 10, 21)), ("15 окт", date(2026, 10, 15)),
    ("3 нояб.", date(2026, 11, 3)), ("в эти выходные", date(2026, 10, 17)), ("в пн", date(2026, 10, 19)),
])
def test_dates_people_actually_write(phrase, want):
    assert find_dates(phrase, TUE) == [want]


@pytest.mark.parametrize("phrase,want", [
    ("к врачу на 20 число", (None, None)), ("на 15 октября лекция", (None, None)),
    ("в половине седьмого", ("18:30", None)), ("в 19 30", ("19:30", None)), ("в 18", ("18:00", None)),
])
def test_time_is_not_taken_from_dates(phrase, want):
    assert parse_time_range(phrase) == want


def test_repeats():
    assert parse_repeat("по вт и чт", TUE) == ("weekly", [1, 3])
    assert parse_repeat("раз в две недели по средам", TUE) == (None, [])     # не «каждую среду»
    assert parse_until("до нового года", TUE) == date(2026, 12, 31)


def test_biweekly_creates_one_lesson_and_says_so(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_EVENT", event_type="other", event_title="Массаж", event_when="в пятницу",
             time_text="в 18", repeat_text="раз в две недели")
    r = run(a.handle_text("Массаж раз в две недели в пятницу в 18"))
    assert "пока не умею" in r.text
    assert len(a.schedule.between(date(2026, 9, 1), date(2026, 12, 31))) == 1


# ---------------------------------------------------------------- поиск и списки

def test_what_about_tomorrow_is_not_search(tmp_path):
    a, llm, _ = make(tmp_path)
    assert a.try_lookup("что у меня про завтра") is None
    assert a.try_lookup("что про деньги") is None
    assert a.try_lookup("что у меня про Сергея") is not None


def test_search_shows_lesson_subject(tmp_path):
    a, llm, _ = make(tmp_path)
    a.schedule.create({"type": "lecture", "date": "2026-09-28", "start_time": "10:00", "discipline": "Анатомия",
                       "location": "Ауд. 305"}, NOW)
    r = a.try_lookup("найди везде анатомия")
    assert "Лекция «Анатомия», Ауд. 305" in r.text


def test_lectures_this_week_only(tmp_path):
    a, llm, _ = make(tmp_path)
    for day in ("2026-09-24", "2026-10-05"):
        a.schedule.create({"type": "lecture", "date": day, "start_time": "10:00"}, NOW)
    r = a.filtered_view("покажи лекции на этой неделе")
    assert "24 сентября" in r.text and "5 октября" not in r.text
