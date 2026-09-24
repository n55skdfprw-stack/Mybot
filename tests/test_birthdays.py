"""Тесты четвёртого этапа — «Дни рождения». Сегодня — четверг, 24 сентября 2026."""

from datetime import date, datetime

from alfred.services.birthdays import parse_birthday

from .test_finance import make as make_fin, run, say
from .test_alfred import TZ, style_ok

TODAY = date(2026, 9, 24)


def make(tmp_path):
    return make_fin(tmp_path)


def at(a, day: date):
    a._clock = lambda: datetime(day.year, day.month, day.day, 12, 0, tzinfo=TZ)


def test_parse_dates():
    assert parse_birthday("12 марта", TODAY) == (12, 3, None)
    assert parse_birthday("5 мая 1998 года", TODAY) == (5, 5, 1998)
    assert parse_birthday("12.03", TODAY) == (12, 3, None)
    assert parse_birthday("05.05.98", TODAY) == (5, 5, 1998)
    assert parse_birthday("не 12, а 14 марта", TODAY) == (14, 3, None)
    assert parse_birthday("31 апреля", TODAY) is None
    assert parse_birthday("когда-нибудь", TODAY) is None


def test_create_and_list_sorted(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "У мамы день рождения 12 марта", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта")
    style_ok(r.text)
    assert r.text == "🎩 Записал, Сэр!\n\n🎂 Мама — 12 марта · через 169 дней"
    r = say(a, llm, "Сергей родился 1 октября 1998", intent="CREATE_BIRTHDAY", person="Сергей",
            bday_text="1 октября 1998")
    assert "🎂 Сергей — 1 октября · через 7 дней · исполнится 28 лет" in r.text
    v = a.birthdays_view()
    lines = v.text.split("\n")[2:]
    assert lines[0].startswith("🎂 Сергей") and lines[1].startswith("🎂 Мама")
    assert v.buttons == [[("✏️ Изменение/удаление", "bd:edit")]]


def test_menu_button(tmp_path):
    a, llm = make(tmp_path)
    r = run(a.handle_text("🎂 Дни рождения"))
    assert r.text.startswith("🎩 Сэр, дней рождения пока нет!")


def test_ai_mistake_becomes_birthday(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "У Димы день рождения 3 ноября", intent="CREATE_TASK", title="День рождения Димы",
            person="Дима")
    assert "Дима — 3 ноября" in r.text and a.tasks.active() == []


def test_asks_whose_and_when(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Запиши день рождения", intent="CREATE_BIRTHDAY")
    assert r.text == "🎩 Разумеется, Сэр! Чей это день рождения?"
    r = say(a, llm, "Папы", intent="ANSWER", answer="Папа")
    assert r.text == "🎩 Разумеется, Сэр! Какого числа день рождения?"
    r = say(a, llm, "20 января", intent="SHOW_BIRTHDAYS")          # ИИ ошибся, но это ответ
    assert "Папа — 20 января" in r.text


def test_correction_right_after(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "У Сергея день рождения 5 мая 1998", intent="CREATE_BIRTHDAY", person="Сергей",
        bday_text="5 мая 1998")
    r = say(a, llm, "Не 5, а 6 мая", intent="UPDATE_FINANCE", target="LAST", new_amount_text="6")
    assert r.text.startswith("🎩 Готово, Сэр! Исправил день рождения!")
    b = a.birthdays.all(a.today())[0]
    assert (b.day, b.month, b.year) == (6, 5, 1998)          # год сохранился


def test_correction_by_name(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Сергей", bday_text="5 мая")
    r = say(a, llm, "У мамы день рождения не 12, а 14 марта", intent="CREATE_BIRTHDAY", person="Мама",
            bday_text="14 марта")
    assert "Мама — 14 марта" in r.text
    assert len(a.birthdays.all(a.today())) == 2


def test_same_person_as_debts(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Сергей должен мне 6000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
        amount_text="6000")
    say(a, llm, "У Сергея день рождения 5 мая", intent="CREATE_BIRTHDAY", person="Сергей", bday_text="5 мая")
    assert len(a.debts.people.all(a.user_id)) == 1


def test_delete_by_button_and_words(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Сергей", bday_text="5 мая")
    r = a.handle_callback("bd:edit")
    r = a.handle_callback(r.buttons[0][0][1])
    assert len(a.birthdays.all(a.today())) == 1
    r = say(a, llm, "Удали день рождения Сергея", intent="DELETE_BIRTHDAY", person="Сергей")
    style_ok(r.text)
    assert a.birthdays.all(a.today()) == [] and "Сергей" in r.text


def test_show_one(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта 1971")
    r = say(a, llm, "Когда день рождения у мамы?", intent="SHOW_BIRTHDAYS", person="Мама")
    assert r.text == "🎩 Разумеется, Сэр!\n\n🎂 Мама — 12 марта · через 169 дней · исполнится 56 лет"


def test_reminder_today_tomorrow_week_together(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="24 сентября 1971")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Олег", bday_text="24 сентября")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Сергей", bday_text="25 сентября")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Иван", bday_text="1 октября 1996")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Петя", bday_text="2 октября")
    r = a.birthday_reminder()
    style_ok(r.text)
    assert r.text == ("🎩 Сэр, позвольте напомнить о днях рождения!\n\n"
                      "🥳 Сегодня:\nМама — 55 лет\nОлег\n\n"
                      "🎈 Завтра, 25 сентября:\nСергей\n\n"
                      "📅 Через неделю, 1 октября:\nИван — 30 лет")


def test_no_reminder_when_nobody(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта")
    assert a.birthday_reminder() is None
    at(a, date(2027, 3, 5))
    assert "Через неделю, 12 марта:\nМама" in a.birthday_reminder().text
    at(a, date(2027, 3, 11))
    assert "Завтра, 12 марта:\nМама" in a.birthday_reminder().text
    at(a, date(2027, 3, 12))
    assert "Сегодня:\nМама" in a.birthday_reminder().text


def test_feb_29_in_normal_year(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Лёша", bday_text="29 февраля 2000")
    at(a, date(2027, 2, 28))
    assert "Сегодня:\nЛёша — 27 лет" in a.birthday_reminder().text


# ---------------------------------------------------------------- версия 4.1: «завтра», «через неделю»

def test_parse_relative():
    assert parse_birthday("завтра", TODAY) == (25, 9, None)
    assert parse_birthday("через неделю", TODAY) == (1, 10, None)
    assert parse_birthday("сегодня", TODAY) == (24, 9, None)
    assert parse_birthday("через 3 дня", TODAY) == (27, 9, None)


def test_birthday_tomorrow_and_in_a_week(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Игорь день рождения завтра", intent="CREATE_BIRTHDAY", person="Игорь", bday_text="завтра")
    assert r.text == "🎩 Записал, Сэр!\n\n🎂 Игорь — 25 сентября · завтра"
    # ИИ не переписал слова о дате — Альфред берёт их из сообщения
    r = say(a, llm, "Виктор день рождения через неделю", intent="CREATE_BIRTHDAY", person="Виктор")
    assert r.text == "🎩 Записал, Сэр!\n\n🎂 Виктор — 1 октября · через 7 дней"
    r = say(a, llm, "Сегодня у Олега день рождения", intent="CREATE_EVENT", event_when="сегодня", person="Олег")
    assert "🥳 Олег — 24 сентября · сегодня" in r.text


def test_answer_relative_date(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Запиши день рождения Пети", intent="CREATE_BIRTHDAY", person="Петя")
    assert r.text == "🎩 Разумеется, Сэр! Какого числа день рождения?"
    r = say(a, llm, "Послезавтра", intent="UNKNOWN")
    assert "Петя — 26 сентября · послезавтра" in r.text
