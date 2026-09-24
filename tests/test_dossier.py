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
    assert r.buttons[0] == [("🔎 Вернуться к поиску", "dos:list")]


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
    assert "День рождения и долги остались на месте" in r.text
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
    assert "остались" not in r.text and a.debts.people.all(a.user_id) == []


def test_phone_as_note_becomes_dossier(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_PERSON", person="Сергей")
    r = say(a, llm, "Номер Сергея 89001234567", intent="CREATE_NOTE", content="Номер Сергея 89001234567",
            person="Сергей")
    assert "Записал: телефон" in r.text and a.notes.all() == []
    assert a.dossier.all()[0].phone == "+7 900 123-45-67"
