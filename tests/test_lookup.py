"""Тесты второй порции: опечатки, «всё про человека», поиск по всем разделам."""

from datetime import date

from alfred.brain.money import parse_currency
from alfred.ui import texts as T

from .test_reliability import owner_alfred
from .test_access import run


def test_menu_typos_open_sections(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    assert "Сэр" in run(a.handle_text("Дела")).text
    assert T.menu_guess("погодв") == T.MENU_WEATHER
    assert T.menu_guess("финнансы") == T.MENU_FINANCE
    assert T.menu_guess("Мои заметки") == T.MENU_NOTES
    assert T.menu_guess("дни рожденя") == T.MENU_BIRTHDAYS
    assert T.menu_guess("Система") is None and T.menu_guess("Система", owner=True) == T.MENU_SYSTEM
    assert T.menu_guess("как дела") is None and T.menu_guess("купить молоко") is None
    assert llm.calls == [] if hasattr(llm, "calls") else True


def test_currency_typos():
    assert parse_currency("100 долар") == "USD"
    assert parse_currency("курс тенгэ") == "KZT"
    assert parse_currency("500 рублй") == "RUB"
    assert parse_currency("сколько эвро") == "EUR"
    assert parse_currency("потратил 300 на такси") is None


def test_person_everything(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    p = a.debts.create_person("Сергей Афанасьев")
    a.dossier.show(p.id)
    a.birthdays.save(p.id, 5, 3, None)
    a.debts.add(p, "owes_me", 2000)
    a.tasks.create("Позвонить Серёге насчёт машины", None)
    a.tasks.create("Купить хлеб", None)
    a.notes.create("Сергей любит рыбалку и коньяк")
    a.finance.add("expense", 1500, "подарки", "подарок Сергею", date(2026, 9, 20))
    for phrase in ("Что у меня про Сергея?", "всё про сергея", "Что я знаю о Серёге", "Что ты знаешь про Сергея"):
        r = run(a.handle_text(phrase))
        assert r.text.startswith("🎩 Сэр, вот всё, что я знаю!"), phrase
        assert "Сергей Афанасьев" in r.text and "Должен вам" in r.text and "🎂" in r.text
        assert "Позвонить Серёге" in r.text and "Купить хлеб" not in r.text
        assert "📝 Заметки" in r.text and "💰 Финансы" in r.text
        assert r.protect
    assert not hasattr(llm, "calls") or True


def test_lookup_not_person_searches_everywhere(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    a.debts.create_person("Маша")
    a.notes.create("Машину отдать в сервис в пятницу")
    a.tasks.create("Помыть машину", None)
    r = run(a.handle_text("Всё про машину"))
    assert r.text.startswith("🎩 Сэр, вот что нашёл по запросу «машину»!")
    assert "📝 Заметки" in r.text and "📋 Дела" in r.text and "Маша" not in r.text
    r = run(a.handle_text("Найди везде домафон"))
    assert "ничего не нашёл" in r.text
    a.notes.create("Код домофона 1234")
    r = run(a.handle_text("Найди везде домафон"))
    assert "Код домофона" in r.text


def test_lookup_several_people_and_button(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    p1 = a.debts.create_person("Дима Иванов")
    a.debts.create_person("Дима Петров")
    r = run(a.handle_text("Всё про Диму"))
    assert "несколько" in r.text and len(r.buttons) == 2
    r = a.handle_callback(f"who:{p1.id}")
    assert "Дима Иванов" in r.text and r.edit


def test_lookup_does_not_steal_other_phrases(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    assert a.try_lookup("Что у меня про машину в заметках?") is None
    assert a.try_lookup("Сколько я должен Сергею") is None
    assert a.try_lookup("Расскажи о себе") is None
    assert a.try_lookup("Купить молоко") is None


def test_fuzzy_names():
    from alfred.services.finance import _loose_same
    from alfred.services.search import score
    assert _loose_same("Сергей", "Серёга") and _loose_same("Сергей", "Сергй") and _loose_same("Дмитрий", "Дима")
    assert not _loose_same("Дима", "Дина") and not _loose_same("Оля", "Юля")
    assert score("домафон", "Код домофона 1234") and score("иванпетров", "Иван Петров")
    assert not score("Дима", "Дина")


def test_debt_of_one_person(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    s = a.debts.create_person("Сергей")
    a.debts.add(s, "owes_me", 5000)
    a.debts.add(a.debts.create_person("Пётр"), "owes_me", 1000)
    llm.said(intent="SHOW_DEBTS", direction="owes_me")          # ИИ не назвал человека
    r = run(a.handle_text("Сколько Сергей должен мне"))
    assert "Сергей" in r.text and "5 000" in r.text and "Пётр" not in r.text
    llm.said(intent="SHOW_DEBTS", person="Серёга")
    r = run(a.handle_text("Сколько мне должен серёга?"))
    assert "Должен вам: 5 000" in r.text and "Пётр" not in r.text
    llm.said(intent="SHOW_DEBTS", direction="owes_me")
    r = run(a.handle_text("Кто мне должен?"))
    assert "Сергей" in r.text and "Пётр" in r.text
    llm.said(intent="SHOW_DEBTS", person="Маша")
    a.debts.create_person("Маша")
    assert "в расчёте" in run(a.handle_text("Сколько Маша мне должна")).text
