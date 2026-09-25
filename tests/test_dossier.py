"""Тесты пятого этапа — «Досье». Сегодня — четверг, 24 сентября 2026."""

from .test_birthdays import make, say, run
from .test_alfred import style_ok


def test_create_and_card(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Создай досье на Сергея Афанасьева", intent="CREATE_PERSON", person="Сергей Афанасьев")
    style_ok(r.text)
    assert r.text == "🎩 Разумеется, Сэр! Досье создано!\n\n👤 Сергей Афанасьев"
    r = say(a, llm, "Запиши номер Сергея +7 900 123 45 67", intent="UPDATE_PERSON", person="Сергей",
            dossier={"phone": "+7 900 123 45 6"})       # ИИ потерял цифру — берём номер из сообщения
    assert r.text == "🎩 Записал, Сэр!\n\n👤 Сергей Афанасьев\nЗаписал: телефон"
    say(a, llm, "Сергей работает тренером и любит хороший кофе", intent="UPDATE_PERSON", person="Сергей",
        dossier={"job": "тренер", "likes": "хороший кофе"})
    say(a, llm, "Он не любит опоздания", intent="UPDATE_PERSON", dossier={"dislikes": "опоздания"})
    say(a, llm, "Сергей должен мне 5000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
        amount_text="5000")
    say(a, llm, "У Сергея день рождения 15 марта", intent="CREATE_BIRTHDAY", person="Сергей", bday_text="15 марта")
    r = say(a, llm, "Покажи досье Сергея", intent="SHOW_PERSON", person="Сергей")
    assert r.text == (
        "🎩 Досье, Сэр!\n\n👤 Сергей Афанасьев\n\n"
        "Имя: Сергей\nФамилия: Афанасьев\n📞 +7 900 123-45-67\n💼 Тренер\n\n"
        "💬 Личная информация\n👍 Любит: хороший кофе\n👎 Не любит: опоздания\n\n"
        "🎂 День рождения: 15 марта · через 172 дня\n🤝 Должен вам: 5 000 ₽")
    assert r.buttons[0][0][0] == "🎁 Идея подарка" and r.buttons[1] == [("🔎 Вернуться к поиску", "dos:list")]


def test_unknown_person_asks_to_create(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Маша любит пионы", intent="UPDATE_PERSON", person="Маша", dossier={"likes": "пионы"})
    assert r.text == "🎩 Разумеется, Сэр! Досье на человека по имени Маша ещё нет. Создать?"
    r = a.handle_callback("dos:yes")
    assert "👤 Маша\nЗаписал: что любит" in r.text
    assert a.dossier.all()[0].likes_dislikes == "+ Пионы"


def test_unknown_person_cancel(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Маша любит пионы", intent="UPDATE_PERSON", person="Маша", dossier={"likes": "пионы"})
    r = a.handle_callback("dos:no")
    assert "Ничего не записываю" in r.text and a.dossier.all() == []


def test_remove_and_flip_opinion(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_PERSON", person="Олег")
    say(a, llm, "Олег любит кофе и футбол", intent="UPDATE_PERSON", person="Олег",
        dossier={"likes": "кофе; футбол"})
    say(a, llm, "Олег не любит кофе", intent="UPDATE_PERSON", person="Олег", dossier={"dislikes": "кофе"})
    c = a.dossier.all()[0]
    assert c.likes_dislikes == "+ Футбол\n- Кофе"
    r = say(a, llm, "Олег больше не любит футбол", intent="UPDATE_PERSON", person="Олег",
            dossier={"likes": "футбол"}, dossier_remove=True)
    assert r.text.startswith("🎩 Готово, Сэр!") and "Убрал: что любит" in r.text
    assert a.dossier.all()[0].likes_dislikes == "- Кофе"


def test_search_by_info(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="UPDATE_PERSON", person="Сергей", dossier={"job": "тренер по гребле"})
    a.handle_callback("dos:yes")
    say(a, llm, "x", intent="UPDATE_PERSON", person="Маша", dossier={"job": "врач"})
    a.handle_callback("dos:yes")
    r = say(a, llm, "Кто работает тренером?", intent="SEARCH_PEOPLE", query="тренером")
    assert r.text.startswith("🎩 Досье, Сэр!\n\n👤 Сергей")


def test_list_and_menu(tmp_path):
    a, llm = make(tmp_path)
    r = run(a.handle_text("🗂️ Досье"))
    assert r.text.startswith("🎩 Сэр, досье пока пусто!")
    say(a, llm, "x", intent="CREATE_PERSON", person="Сергей")
    say(a, llm, "x", intent="CREATE_PERSON", person="Анна")
    r = run(a.handle_text("🗂️ Досье"))
    assert [row[0][0] for row in r.buttons] == ["👤 Анна", "👤 Сергей"]


def test_many_people_asks_whom(tmp_path):
    a, llm = make(tmp_path)
    for n in ["Анна", "Борис", "Вера", "Глеб", "Дина", "Егор", "Жанна", "Зоя", "Игорь", "Катя", "Лев"]:
        say(a, llm, "x", intent="CREATE_PERSON", person=n)
    r = run(a.handle_text("🗂️ Досье"))
    assert "🔎 Кого ищем?" in r.text and not r.buttons
    llm.said(intent="SHOW_PERSON", person="Вера")      # короткий ответ — это ответ на вопрос
    r = run(a.handle_text("Вера"))
    assert "👤 Вера" in r.text


def test_delete_keeps_birthday_and_debt(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="UPDATE_PERSON", person="Сергей", dossier={"job": "тренер"})
    a.handle_callback("dos:yes")
    say(a, llm, "Сергей должен мне 5000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
        amount_text="5000")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Сергей", bday_text="15 марта")
    r = say(a, llm, "Удали досье Сергея", intent="DELETE_PERSON", person="Сергей")
    assert r.text == "🎩 Сэр, вы действительно хотите удалить всё досье: Сергей?"
    r = a.handle_callback(r.buttons[0][0][1])
    assert r.text == "🎩 Удалил досье, Сэр!\n\n❌ Сергей"
    assert a.dossier.all() == []
    assert len(a.debts.active()) == 1 and len(a.birthdays.all(a.today())) == 1
    # снова пишем о нём — досье возвращается, без старых сведений
    say(a, llm, "Сергей любит кофе", intent="UPDATE_PERSON", person="Сергей", dossier={"likes": "кофе"})
    c = a.dossier.all()[0]
    assert c.job is None and c.likes_dislikes == "+ Кофе"


def test_delete_person_without_links_is_gone(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_PERSON", person="Анна")
    r = a.handle_callback("dos:delyes:" + str(a.dossier.all()[0].id))
    assert a.debts.people.all(a.user_id) == []


def test_phone_as_note_becomes_dossier(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_PERSON", person="Сергей")
    r = say(a, llm, "Номер Сергея 89001234567", intent="CREATE_NOTE", content="Номер Сергея 89001234567",
            person="Сергей")
    assert "Записал: телефон" in r.text and a.notes.all() == []
    assert a.dossier.all()[0].phone == "+7 900 123-45-67"


def test_deleted_debts_and_birthdays_leave_no_ghosts(tmp_path):
    """Живая ошибка: после удаления долгов и дней рождения люди оставались в досье пустыми."""
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Мама", bday_text="12 марта")
    say(a, llm, "Максим должен мне 3000", intent="CREATE_DEBT", person="Максим", direction="owes_me",
        amount_text="3000")
    say(a, llm, "x", intent="CREATE_BIRTHDAY", person="Лёша", bday_text="15 сентября")
    say(a, llm, "x", intent="CREATE_PERSON", person="Анна")              # досье заведено явно
    say(a, llm, "x", intent="UPDATE_PERSON", person="Лёша", dossier={"job": "тренер"})
    # удаляем день рождения мамы и закрываем долг Максима
    a.handle_callback("bd:del:" + str(next(b.id for b in a.birthdays.all(a.today())
                                          if a._name(b.person_id) == "Мама")))
    say(a, llm, "Максим вернул долг", intent="REPAY_DEBT", person="Максим", direction="owes_me")
    names = [c.full_name for c in a.dossier.all()]
    assert names == ["Анна", "Лёша"]
    # Лёша без дня рождения остаётся — о нём есть сведения
    a.handle_callback("bd:del:" + str(a.birthdays.all(a.today())[0].id))
    assert [c.full_name for c in a.dossier.all()] == ["Анна", "Лёша"]


# ---------------------------------------------------------------- 8.5: имя и перенос заметки

def test_rename_after_birthday(tmp_path):
    """Живая ошибка: «25 декабря др у Дани» записало «Дани», а «исправь имя на Даня» спросило дату."""
    a, llm = make(tmp_path)
    say(a, llm, "25 декабря др у Дани", intent="CREATE_BIRTHDAY", person="Дани", bday_text="25 декабря")
    r = run(a.handle_text("исправь имя на Даня"))            # без ИИ
    style_ok(r.text)
    assert r.text.startswith("🎩 Готово, Сэр! Исправил имя!\n\n👤 Дани → Даня\n🎂 Даня — 25 декабря")
    assert a._name(a.birthdays.all(a.today())[0].person_id) == "Даня"


def test_rename_by_name(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_PERSON", person="Серега")
    r = run(a.handle_text("Переименуй Серега в Сергей Афанасьев"))
    assert "👤 Серега → Сергей Афанасьев" in r.text


def test_move_note_to_dossier(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Вася Пупкин должник, так и запиши", intent="CREATE_NOTE", content="Вася Пупкин — должник")
    llm.said(intent="UPDATE_PERSON", person="Вася Пупкин", dossier={"facts": "должник"})
    r = run(a.handle_text("удали из заметок и напиши в досье"))
    assert r.text.startswith("🎩 Готово, Сэр! Перенёс заметку в досье.\n\n👤 Вася Пупкин")
    assert a.notes.all() == []
    assert a.dossier.all()[0].important_facts == "Должник"


def test_rename_poменяй_and_fact_about_known_person(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "У Дианы др 5 мая", intent="CREATE_BIRTHDAY", person="Диана", bday_text="5 мая")
    r = run(a.handle_text("Поменяй Диана на Диани"))
    assert "👤 Диана → Диани" in r.text
    # Альфред спросил дату, а человек написал о другом — это не ответ
    llm.said(intent="UPDATE_BIRTHDAY", person="Диани")
    run(a.handle_text("Исправь день рождения Диани"))
    llm.said(intent="ANSWER", answer="5 мая")                  # ИИ «придумал» ответ
    llm.said(intent="CREATE_NOTE", content="Диани зануда")      # после повторного разбора
    r = run(a.handle_text("Диани зануда"))
    assert r.text.startswith("🎩 Записал, Сэр!\n\n👤 Диани") and a.notes.all() == []
    assert a.dossier.all()[0].important_facts == "Зануда"


def test_move_right_after_note(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Олег Смирнов должник", intent="CREATE_NOTE", content="Олег Смирнов — должник")
    llm.said(intent="UPDATE_PERSON", person="Олег Смирнов", dossier={"facts": "должник"})
    r = run(a.handle_text("Запиши это в досье"))
    assert r.text.startswith("🎩 Готово, Сэр! Перенёс заметку в досье.") and a.notes.all() == []


def test_rename_back_and_fact_not_dislike(tmp_path):
    """Живые ошибки: «Поменяй обратно» изменило заметку; «Диани зануда» записалось в «не любит»."""
    a, llm = make(tmp_path)
    say(a, llm, "Паспорт лежит в верхнем ящике", intent="CREATE_NOTE", content="Паспорт лежит в верхнем ящике")
    say(a, llm, "У Дианы др 5 мая", intent="CREATE_BIRTHDAY", person="Диана", bday_text="5 мая")
    run(a.handle_text("Поменяй Диана на Диани"))
    r = run(a.handle_text("Поменяй обратно"))                     # без ИИ
    assert r.text == "🎩 Готово, Сэр! Вернул как было!\n\n👤 Диани → Диана"
    assert a.notes.all()[0].content == "Паспорт лежит в верхнем ящике"
    r = say(a, llm, "Диана зануда", intent="UPDATE_PERSON", person="Диана", dossier={"dislikes": "зануда"})
    assert "Записал: важные факты" in r.text
    c = a.dossier.all()[0]
    assert c.important_facts == "Зануда" and not c.likes_dislikes
    say(a, llm, "Диана не любит опоздания", intent="UPDATE_PERSON", person="Диана", dossier={"dislikes": "опоздания"})
    assert a.dossier.all()[0].likes_dislikes == "- Опоздания"


def test_rename_to_same_name(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "У Дианы др 5 мая", intent="CREATE_BIRTHDAY", person="Диана", bday_text="5 мая")
    r = run(a.handle_text("Поменяй Диана на Диана"))
    assert r.text == "🎩 Сэр, человека уже так и зовут: Диана!"
