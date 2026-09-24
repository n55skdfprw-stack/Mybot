"""Тесты четвёртого этапа — «Дни рождения». Сегодня — четверг, 24 сентября 2026."""

from datetime import date, datetime

from alfred.services.birthdays import parse_birthday

from .test_finance import make as make_fin, run, say as _say
from .test_alfred import TZ, style_ok

TODAY = date(2026, 9, 24)


def say(a, llm, text, **ai):
    """Заглушка «x» превращается в настоящую фразу: «Мама день рождения 12 марта»."""
    if text == "x" and ai.get("bday_text"):
        text = f"{ai.get('person', '')} день рождения {ai['bday_text']}"
    return _say(a, llm, text, **ai)


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
    assert v.buttons == [[("🎁 Идея подарка", "bd:giftpick")], [("✏️ Изменение/удаление", "bd:edit")]]


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


def test_ai_invented_date_is_ignored(tmp_path):
    """Живая ошибка: «Запиши день рождения Оли» записало «послезавтра» из прошлого сообщения."""
    a, llm = make(tmp_path)
    say(a, llm, "Петя день рождения послезавтра", intent="CREATE_BIRTHDAY", person="Петя", bday_text="послезавтра")
    r = say(a, llm, "Запиши день рождения Оли", intent="CREATE_BIRTHDAY", person="Оля", bday_text="послезавтра")
    assert r.text == "🎩 Разумеется, Сэр! Какого числа день рождения?"
    r = say(a, llm, "3 июня", intent="ANSWER", answer="3 июня")
    assert "Оля — 3 июня" in r.text


def test_answer_uses_own_words_not_ai_guess(tmp_path):
    """Живая ошибка: на «Через 3 дня» ИИ вписал в ответ «3 июля» из прошлой записи про Олю."""
    a, llm = make(tmp_path)
    say(a, llm, "У Оли день рождения 3 июля", intent="CREATE_BIRTHDAY", person="Оля", bday_text="3 июля")
    r = say(a, llm, "Запиши день рождения Димы", intent="CREATE_BIRTHDAY", person="Дима")
    assert r.text == "🎩 Разумеется, Сэр! Какого числа день рождения?"
    r = say(a, llm, "Через 3 дня", intent="ANSWER", answer="3 июля")
    assert "Дима — 27 сентября · через 3 дня" in r.text


# ---------------------------------------------------------------- 8.2: 🎁 идея подарка

IDEAS = ("1. 🌸 **Букет пионов** — она их любит\n2. 🪴 Набор для сада — интерес к даче\n"
         "3. 🎟 Билеты в театр — впечатление\n4. 🧣 Тёплый шарф — практично\n5. 📷 Фотокнига — память")


def test_gift_ideas_use_dossier(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта 1971")
    say(a, llm, "Мама любит пионы", intent="UPDATE_PERSON", person="Мама", dossier={"likes": "пионы"})
    say(a, llm, "Мама не любит сладкое", intent="UPDATE_PERSON", person="Мама", dossier={"dislikes": "сладкое"})
    prompts = []
    orig = llm.complete

    async def spy(system, user, pro=False):
        prompts.append(user)
        return await orig(system, user, pro)
    llm.complete = spy
    llm.queue.append(IDEAS)
    r = run(a.handle_callback_async(a.birthdays_view().buttons[0][0][1]))   # 🎁 → выбор человека
    r = run(a.handle_callback_async(r.buttons[0][0][1]))
    style_ok(r.text)
    assert r.text.startswith("🎩 Идеи подарка для: Мама, Сэр!\n\n🎂 12 марта · через 169 дней · исполнится 56 лет\n"
                             "📂 Учёл из досье: любит: пионы; не любит: сладкое")
    assert "🌸 Букет пионов — она их любит" in r.text and "**" not in r.text and "1." not in r.text
    assert "Исполнится: 56" in prompts[0] and "НЕ любит (не дарить!): Сладкое" in prompts[0]
    assert r.buttons[0][0] == ("🔄 Ещё идеи", r.buttons[0][0][1])


def test_gift_without_dossier_and_reminder_button(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Олег", bday_text="25 сентября")
    rem = a.birthday_reminder()
    assert rem.buttons == [[("🎁 Подарок: Олег", rem.buttons[0][0][1])]]
    llm.queue.append(IDEAS)
    r = run(a.handle_callback_async(rem.buttons[0][0][1]))
    assert "📂 В досье о человеке пока ничего нет — идеи общие." in r.text


def test_gift_ai_down(tmp_path):
    from alfred.brain.llm_client import LLMError
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Олег", bday_text="25 сентября")
    llm.queue.append(LLMError("нет связи"))
    r = run(a.handle_callback_async(f"bd:gift:{a.birthdays.all(a.today())[0].id}"))
    assert r.text.startswith("🎩 Прошу прощения, Сэр! Не удалось придумать")
