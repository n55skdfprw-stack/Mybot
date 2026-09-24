"""Тесты третьего этапа — «Ваши финансы». Сегодня — четверг, 24 сентября 2026."""

import asyncio
from datetime import date, datetime

import pytest

from alfred.brain.brain import Brain
from alfred.core.alfred import Alfred
from alfred.database.db import Database
from alfred.database.finance_repo import DebtRepository, OperationRepository, PeopleRepository
from alfred.database.repositories import ContextRepository, NoteRepository, TaskRepository, UserRepository
from alfred.database.schedule_repo import EventRepository, NotificationRepository, RuleRepository
from alfred.services.currency import CurrencyService
from alfred.services.finance import DebtService, FinanceService
from alfred.services.notes import NoteService
from alfred.services.schedule import ScheduleService
from alfred.services.tasks import TaskService

from .test_alfred import TZ, FakeLLM, style_ok

CBR = {"Date": "2026-09-24T11:30:00+03:00", "Valute": {
    "USD": {"Nominal": 1, "Value": 82.45, "Previous": 82.14},
    "EUR": {"Nominal": 1, "Value": 90.12, "Previous": 90.27},
    "CNY": {"Nominal": 1, "Value": 11.38, "Previous": 11.36},
    "KZT": {"Nominal": 100, "Value": 16.5, "Previous": 16.4},
    "TRY": {"Nominal": 10, "Value": 20.5, "Previous": 20.4},
    "KRW": {"Nominal": 1000, "Value": 61.2, "Previous": 61.0},
}}


def make(tmp_path, cbr_ok=True):
    db = Database(str(tmp_path / "f.db"))
    db.migrate()
    uid = UserRepository(db).ensure(1, "Europe/Moscow", "Санкт-Петербург")
    llm = FakeLLM()

    async def fetch():
        if not cbr_ok:
            raise ConnectionError("нет сети")
        return CBR

    a = Alfred(Brain(llm), TaskService(TaskRepository(db), uid), NoteService(NoteRepository(db), uid),
               ScheduleService(EventRepository(db), RuleRepository(db), NotificationRepository(db), uid),
               ContextRepository(db), uid, TZ, clock=lambda: datetime(2026, 9, 24, 12, 0, tzinfo=TZ),
               finance=FinanceService(OperationRepository(db), uid),
               debts=DebtService(DebtRepository(db), PeopleRepository(db), uid),
               currency=CurrencyService(fetch=fetch))
    return a, llm


def run(coro):
    return asyncio.run(coro)


def say(a, llm, text, **ai):
    """Сообщение-заглушка «x» превращается в настоящую фразу, как её написал бы человек."""
    if text in ("x", "y") and ai.get("intent") in ("CREATE_EXPENSE", "CREATE_INCOME"):
        verb = "Потратил" if ai["intent"] == "CREATE_EXPENSE" else "Получил"
        text = f"{verb} {ai.get('amount_text', '')}" + (f" на {ai['category']}" if ai.get("category") else "")
    llm.said(**ai)
    return run(a.handle_text(text))


# ---------------------------------------------------------------- расходы и доходы

def test_expense_as_in_spec(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Потратил 2500 на продукты", intent="CREATE_EXPENSE", amount_text="2500", category="продукты")
    assert r.text == "🎩 Записал, Сэр!\nРасход: 2 500 ₽ — продукты!"


def test_expense_without_category_asks(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Потратил 700", intent="CREATE_EXPENSE", amount_text="700")
    assert r.text == "🎩 Разумеется, Сэр! На что был расход?"
    r = say(a, llm, "На такси", intent="ANSWER", answer="такси")
    assert "700 ₽ — такси" in r.text


def test_income_without_source_not_asked(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Получил 120000", intent="CREATE_INCOME", amount_text="120000")
    assert r.text == "🎩 Записал, Сэр!\nДоход: 120 000 ₽!"
    r = say(a, llm, "Получил зарплату 120к", intent="CREATE_INCOME", amount_text="120к", category="зарплата")
    assert "120 000 ₽ — зарплата" in r.text


def test_amount_forms_and_yesterday(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Вчера потратил 2,5 тысячи на кафе", intent="CREATE_EXPENSE", amount_text="2,5 тысячи",
            category="кафе", op_when="вчера")
    assert "2 500 ₽ — рестораны (23 сентября)" in r.text


def test_purchase_phrase_is_expense_not_task(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Купил кроссовки за 8000", intent="CREATE_TASK", title="Купить кроссовки")
    assert "Расход: 8 000 ₽ — одежда" in r.text and a.tasks.active() == []


def test_foreign_expense_converted(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Потратил 50 евро на ужин", intent="CREATE_EXPENSE", amount_text="50 евро",
            currency="евро", category="ужин")
    assert "4 506 ₽ (50 €) — рестораны" in r.text


def test_foreign_expense_without_rates(tmp_path):
    a, llm = make(tmp_path, cbr_ok=False)
    r = say(a, llm, "Потратил 50 евро на ужин", intent="CREATE_EXPENSE", amount_text="50 евро", category="ужин")
    assert "курс ЦБ" in r.text and a.finance.latest() == []


# ---------------------------------------------------------------- остаток и статистика

def test_balance_can_be_negative(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Получил 5000", intent="CREATE_INCOME", amount_text="5000")
    say(a, llm, "Потратил 10000 на аренду", intent="CREATE_EXPENSE", amount_text="10000", category="аренда")
    r = say(a, llm, "Какой у меня остаток?", intent="SHOW_BALANCE")
    assert r.text == "🎩 Ваш остаток, Сэр: −5 000 ₽!"


def test_statistics(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_INCOME", amount_text="250000", category="зарплата")
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="18400", category="продукты")
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="7200", category="такси")
    r = run(a.handle_text("💰 Ваши финансы"))
    style_ok(r.text)
    assert "💰 Остаток: 224 400 ₽" in r.text
    assert "📈 Доходы за месяц: 250 000 ₽" in r.text and "📉 Расходы за месяц: 25 600 ₽" in r.text
    assert r.text.index("🛒 Продукты — 18 400 ₽") < r.text.index("🚕 Такси — 7 200 ₽")
    assert r.buttons


def test_debt_is_not_income_or_expense(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Сергей должен мне 5000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
        amount_text="5000")
    say(a, llm, "Сергей вернул мне 5000", intent="REPAY_DEBT", person="Сергей", direction="owes_me",
        amount_text="5000")
    assert a.finance.balance() == 0 and a.finance.latest() == []


# ---------------------------------------------------------------- поиск

def test_search_category_period(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="1000", category="продукты")
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="500", category="продукты", op_when="вчера")
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="300", category="такси")
    r = say(a, llm, "Сколько я потратил на продукты за неделю?", intent="SEARCH_FINANCE", category="продукты",
            period_text="за неделю")
    assert r.text.startswith("🎩 На продукты за неделю — 1 500 ₽, Сэр!")


def test_vague_period_asks(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Сколько я потратил за последнее время?", intent="SEARCH_FINANCE",
            period_text="за последнее время")
    assert r.text == "🎩 Сэр, за какой период показать данные?"


# ---------------------------------------------------------------- исправления и удаление

def test_correction_amount(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Потратил 5000 на продукты", intent="CREATE_EXPENSE", amount_text="5000", category="продукты")
    r = say(a, llm, "Не 5000, а 3000", intent="UPDATE_FINANCE", target="LAST", new_amount_text="3000")
    style_ok(r.text)
    assert a.finance.latest()[0].amount == 3000 and len(a.finance.latest()) == 1


def test_correction_category(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Потратил 500 на продукты", intent="CREATE_EXPENSE", amount_text="500", category="продукты")
    say(a, llm, "Это было на такси", intent="UPDATE_FINANCE", target="LAST", new_category="такси")
    assert a.finance.latest()[0].category == "Такси"


def test_delete_last_expense_keeps_income(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="300", category="кофе")
    say(a, llm, "x", intent="CREATE_INCOME", amount_text="1000")
    r = say(a, llm, "Удали последний расход", intent="DELETE_FINANCE", target="LAST", op_type="expense")
    style_ok(r.text)
    assert [o.type for o in a.finance.latest()] == ["income"]


def test_delete_two_last_needs_confirmation(tmp_path):
    a, llm = make(tmp_path)
    for amount in ("100", "200", "300"):
        say(a, llm, "x", intent="CREATE_EXPENSE", amount_text=amount, category="кофе")
    r = say(a, llm, "Удали две последние записи", intent="DELETE_FINANCE", target="LAST", count=2)
    assert r.text.startswith("🎩 Сэр, вы действительно хотите удалить эти записи (2)?")
    a.handle_callback(r.buttons[0][0][1])
    assert [o.amount for o in a.finance.latest()] == [100]


def test_edit_button_deletes(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="100", category="кофе")
    r = a.handle_callback("fin:edit")
    a.handle_callback(r.buttons[0][0][1])
    assert a.finance.latest() == []


# ---------------------------------------------------------------- долги

def test_debts_flow(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Сергей должен мне 5000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
            amount_text="5000")
    style_ok(r.text)
    say(a, llm, "Андрей должен мне 7500", intent="CREATE_DEBT", person="Андрей", direction="owes_me",
        amount_text="7500")
    say(a, llm, "Я должен Максиму 5000", intent="CREATE_DEBT", person="Максим", direction="i_owe",
        amount_text="5000")
    r = say(a, llm, "Сергей вернул мне 2000", intent="REPAY_DEBT", person="Сергей", direction="owes_me",
            amount_text="2000")
    assert "погашено 2 000 ₽, осталось 3 000 ₽" in r.text
    r = say(a, llm, "Мне должны", intent="SHOW_DEBTS", direction="owes_me")
    assert r.text == "🎩 Вот кто вам должен, Сэр!\n\n🤝 Андрей — 7 500 ₽\n🤝 Сергей — 3 000 ₽"
    r = say(a, llm, "Я должен", intent="SHOW_DEBTS", direction="i_owe")
    assert r.text == "🎩 Вот кому вы должны, Сэр!\n\n💸 Максим — 5 000 ₽"


def test_full_repayment_removes_debt(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_DEBT", person="Сергей", direction="owes_me", amount_text="5000")
    r = say(a, llm, "Сергей вернул весь долг", intent="REPAY_DEBT", person="Сергей", direction="owes_me")
    assert "полностью погашен" in r.text and a.debts.active() == []


def test_same_person_dative_is_found(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_DEBT", person="Максим", direction="i_owe", amount_text="3000")
    say(a, llm, "Я вернул Максиму 1000", intent="REPAY_DEBT", person="Максиму", amount_text="1000")
    [(p, d)] = a.debts.active()
    assert p.first_name == "Максим" and d.amount == 2000 and len(a.debts.people.all(a.user_id)) == 1


def test_ambiguous_name_asks(tmp_path):
    a, llm = make(tmp_path)
    a.debts.create_person("Сергей Афанасьев")
    a.debts.create_person("Сергей Петров")
    r = say(a, llm, "Сергей должен мне 1000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
            amount_text="1000")
    assert r.text == "🎩 Сэр, уточните, пожалуйста, какой Сергей?" and len(r.buttons) == 3
    pid = a.debts.find_people("Сергей Петров")[0].id
    a.handle_callback(f"pick:{pid}")
    [(p, d)] = a.debts.active()
    assert p.last_name == "Петров" and d.amount == 1000


def test_direction_from_words(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Я должен Игорю 3000", intent="CREATE_DEBT", person="Игорь", amount_text="3000")
    assert a.debts.active("i_owe")


# ---------------------------------------------------------------- валюты

def test_rates_view(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Курсы валют", intent="SHOW_CURRENCY_RATES")
    assert r.text.startswith("🎩 Курсы ЦБ на 24 сентября, Сэр!")
    assert "🇺🇸 Доллар — 82,45 ₽ (▲ 0,31)" in r.text and "🇪🇺 Евро — 90,12 ₽ (▼ 0,15)" in r.text


@pytest.mark.parametrize("text,ai,expected", [
    ("Сколько 100 долларов в рублях?", dict(amount_text="100", currency="доллары"), "100 $ = 8 245 ₽"),
    ("100 usd", dict(), "100 $ = 8 245 ₽"),
    ("5000 рублей в евро", dict(amount_text="5000", currency="рубли", convert_to="евро"), "5 000 ₽ = 55,48 €"),
    ("Переведи 50 евро в доллары", dict(amount_text="50", currency="евро", convert_to="доллары"), "50 € = 54,65 $"),
    ("Сколько будет 1000 тенге?", dict(amount_text="1000", currency="тенге"), "1 000 ₸ = 165 ₽"),
])
def test_convert(tmp_path, text, ai, expected):
    a, llm = make(tmp_path)
    r = say(a, llm, text, intent="CONVERT_CURRENCY", **ai)
    assert expected in r.text and r.text.endswith("по курсу ЦБ!")


def test_rates_button_async(tmp_path):
    a, _ = make(tmp_path)
    r = run(a.handle_callback_async("fin:rates"))
    assert r.edit and "Курсы ЦБ" in r.text


def test_rates_unavailable(tmp_path):
    a, llm = make(tmp_path, cbr_ok=False)
    r = say(a, llm, "Курс доллара", intent="SHOW_CURRENCY_RATES")
    assert "недоступен" in r.text


def test_reset_wipes_finance(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="100", category="кофе")
    say(a, llm, "x", intent="CREATE_DEBT", person="Сергей", direction="owes_me", amount_text="5000")
    a.handle_callback("confirm:reset_all")
    assert a.finance.latest() == [] and a.debts.active() == []


# ---------------------------------------------------------------- живые ошибки 24.09, 01:18

def test_ai_invented_category_is_ignored(tmp_path):
    """«Потратил 700» записалось как «продукты» — ИИ взял категорию из прошлого сообщения."""
    a, llm = make(tmp_path)
    say(a, llm, "Потратил 2500 на продукты", intent="CREATE_EXPENSE", amount_text="2500", category="продукты")
    r = say(a, llm, "Потратил 700", intent="CREATE_EXPENSE", amount_text="700", category="продукты")
    assert r.text == "🎩 Разумеется, Сэр! На что был расход?"
    r = say(a, llm, "На такси", intent="ANSWER", answer="такси")
    assert "700 ₽ — такси" in r.text


def test_correction_picks_record_by_old_amount(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="2500", category="продукты")
    say(a, llm, "y", intent="CREATE_EXPENSE", amount_text="700", category="продукты")
    say(a, llm, "Потратил 900 на продукты", intent="CREATE_EXPENSE", amount_text="900", category="продукты")
    r = say(a, llm, "Не 700 а 800", intent="UPDATE_FINANCE", target="продукты", new_amount_text="800")
    assert not r.buttons and "800 ₽" in r.text
    assert sorted(o.amount for o in a.finance.latest()) == [800, 900, 2500]


@pytest.mark.parametrize("text,ai_amount,expected", [
    ("Получил 120к", "120", "120 000 ₽"),       # ИИ «потерял» букву к
    ("Потратил 5к на продукты", "5к", "5 000 ₽"),
    ("Потратил 5 тр на продукты", "5", "5 000 ₽"),
    ("Потратил 2 косаря на продукты", "2", "2 000 ₽"),
    ("Потратил 1,5к на продукты", "1,5к", "1 500 ₽"),
    ("Получил 1.2 млн", "1.2 млн", "1 200 000 ₽"),
])
def test_k_suffix_from_message(tmp_path, text, ai_amount, expected):
    a, llm = make(tmp_path)
    intent = "CREATE_INCOME" if text.startswith("Получил") else "CREATE_EXPENSE"
    r = say(a, llm, text, intent=intent, amount_text=ai_amount, category="продукты" if "продукт" in text else None)
    assert expected in r.text


# ---------------------------------------------------------------- версия 3.2

def test_taxi_and_transport_card_are_separate(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Потратил 800 на такси", intent="CREATE_EXPENSE", amount_text="800", category="такси")
    assert r.text == "🎩 Записал, Сэр!\nРасход: 800 ₽ — такси!"
    r = say(a, llm, "Пополнил БСК на 1000", intent="CREATE_EXPENSE", amount_text="1000", category="БСК")
    assert r.text == "🎩 Записал, Сэр!\nРасход: 1 000 ₽ — транспорт!"
    r = say(a, llm, "Потратил 70 на метро", intent="CREATE_EXPENSE", amount_text="70", category="метро")
    assert "— транспорт" in r.text


def test_debts_header(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Сергей должен мне 6000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
        amount_text="6000")
    r = a.handle_callback("fin:debts")
    assert r.text.startswith("🎩 Разумеется, Сэр!\n\n🤝 Вам должны:\nСергей — 6 000 ₽")


def test_debt_correction_right_after(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Сергей должен мне 5000", intent="CREATE_DEBT", person="Сергей", direction="owes_me",
        amount_text="5000")
    # ИИ ошибся и решил, что это исправление расхода — Альфред всё равно правит долг
    r = say(a, llm, "Не 5000 а 6000", intent="UPDATE_FINANCE", target="LAST", new_amount_text="6000")
    style_ok(r.text)
    assert r.text == "🎩 Готово, Сэр! Исправил долг!\n\n🤝 Вам должен: Сергей — 6 000 ₽"
    [(p, d)] = a.debts.active()
    assert d.amount == 6000 and a.finance.latest() == []


def test_debt_correction_by_name_not_added(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Я должен Максиму 3000", intent="CREATE_DEBT", person="Максим", direction="i_owe",
        amount_text="3000")
    say(a, llm, "Потратил 500 на продукты", intent="CREATE_EXPENSE", amount_text="500", category="продукты")
    r = say(a, llm, "Я должен Максиму не 3000, а 2500", intent="CREATE_DEBT", person="Максим",
            direction="i_owe", amount_text="2500")
    assert r.text == "🎩 Готово, Сэр! Исправил долг!\n\n💸 Ваш долг: Максим — 2 500 ₽"
    assert a.finance.latest()[0].amount == 500


def test_debt_correction_asks_which(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_DEBT", person="Сергей", direction="owes_me", amount_text="5000")
    say(a, llm, "x", intent="CREATE_DEBT", person="Максим", direction="i_owe", amount_text="3000")
    a._set_last("debt", None)
    r = say(a, llm, "Исправь долг на 4000", intent="UPDATE_DEBT", new_amount_text="4000")
    assert "какой именно долг" in r.text and len(r.buttons) == 3
    debt_id = int(r.buttons[1][0][1].split(":")[1])
    r = a.handle_callback(f"pick:{debt_id}")
    assert "Исправил долг" in r.text
    assert sorted(d.amount for _, d in a.debts.active()) == [4000, 5000]


def test_edit_screen_deletes_debt(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_DEBT", person="Сергей", direction="owes_me", amount_text="6000")
    say(a, llm, "x", intent="CREATE_EXPENSE", amount_text="800", category="такси")
    r = a.handle_callback("fin:edit")
    datas = [row[0][1] for row in r.buttons]
    assert any(d.startswith("fin:deldebt:") for d in datas) and any(d.startswith("fin:del:") for d in datas)
    deldebt = next(d for d in datas if d.startswith("fin:deldebt:"))
    r = a.handle_callback(deldebt)
    assert a.debts.active() == [] and len(a.finance.latest()) == 1


def test_delete_last_debt_by_words(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "x", intent="CREATE_DEBT", person="Сергей", direction="owes_me", amount_text="6000")
    r = say(a, llm, "Удали его", intent="DELETE_FINANCE", target="LAST")
    assert r.text.startswith("🎩 Удалил долг, Сэр!") and a.debts.active() == []


# ---------------------------------------------------------------- версия 3.3

def test_topup_transport_card_is_expense(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Пополнил бск на 1000", intent="CREATE_INCOME", amount_text="1000", category="транспорт")
    assert r.text == "🎩 Записал, Сэр!\nРасход: 1 000 ₽ — транспорт!"
    assert a.finance.latest()[0].type == "expense"


def test_real_income_not_touched(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Получил зарплату 100000", intent="CREATE_INCOME", amount_text="100000", category="зарплата")
    assert r.text.startswith("🎩 Записал, Сэр!\nДоход:")


def test_short_answer_is_not_search(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Потратил на такси 500", intent="CREATE_EXPENSE", amount_text="500", category="такси")
    r = say(a, llm, "Потратил 1500", intent="CREATE_EXPENSE", amount_text="1500")
    assert r.text == "🎩 Разумеется, Сэр! На что был расход?"
    # ИИ ошибся и принял ответ за поиск — Альфред всё равно понимает, что это ответ
    r = say(a, llm, "Такси", intent="SEARCH_FINANCE", category="такси")
    assert r.text == "🎩 Записал, Сэр!\nРасход: 1 500 ₽ — такси!"
    assert sorted(o.amount for o in a.finance.latest()) == [500, 1500]


def test_question_after_pending_still_works(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Потратил 1500", intent="CREATE_EXPENSE", amount_text="1500")
    r = say(a, llm, "Сколько я потратил на такси за месяц?", intent="SEARCH_FINANCE", category="такси",
            period_text="за месяц")
    assert "за месяц" in r.text


# ---------------------------------------------------------------- версия 3.6: группы

def test_same_day_same_category_grouped(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Потратил 80 на автобус", intent="CREATE_EXPENSE", amount_text="80", category="автобус")
    say(a, llm, "Потратил 500 на такси", intent="CREATE_EXPENSE", amount_text="500", category="такси")
    say(a, llm, "Потратил 80 на автобус", intent="CREATE_EXPENSE", amount_text="80", category="автобус")
    r = a.handle_callback("fin:exp")
    labels = [row[0][0] for row in r.buttons]
    assert any("Транспорт · −160 ₽ ×2" in l for l in labels), labels
    assert any("Такси · −500 ₽" in l and "×" not in l for l in labels), labels
    grp = next(row[0][1] for row in r.buttons if "Транспорт" in row[0][0])
    r = a.handle_callback(grp)
    assert "Транспорт" in r.text and "160 ₽" in r.text
    assert r.text.count("🕐") == 2 and r.text.count("−80 ₽") == 2
    # удаляем одну поездку прямо из группы
    r = a.handle_callback(r.buttons[0][0][1])
    assert r.text.count("🕐") == 1


def test_group_shows_transport_kind(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Пополнил бск на 1000", intent="CREATE_EXPENSE", amount_text="1000", category="транспорт")
    say(a, llm, "Потратил 85 на автобус", intent="CREATE_EXPENSE", amount_text="85", category="автобус")
    say(a, llm, "86 на метро", intent="CREATE_EXPENSE", amount_text="86", category="метро")
    r = a.handle_callback("fin:exp")
    r = a.handle_callback(r.buttons[0][0][1])
    lines = [l for l in r.text.split("\n") if l.startswith("🕐")]
    assert lines[0].endswith("−1 000 ₽ БСК") and lines[1].endswith("−85 ₽ Автобус") and lines[2].endswith("−86 ₽ Метро")
    assert r.buttons[1][0][0].endswith("85 ₽ Автобус")


# ---------------------------------------------------------------- 8.6: курс любой валюты

def test_quick_rate_one_word(tmp_path):
    """Живая ошибка: на «лира» Альфред ответил «раздел обустраиваю»."""
    a, llm = make(tmp_path)
    r = run(a.handle_text("лира"))                       # без ИИ
    style_ok(r.text)
    assert r.text == ("🎩 Курс ЦБ на 24 сентября, Сэр!\n\n🇹🇷 Лира — 2,05 ₽ (▲ 0,01)\n\n"
                      "💱 Пересчитать можно так: «100 TRY в рублях».")
    r = run(a.handle_text("Курс вона?"))
    assert "🇰🇷 Вона — за 100: 6,12 ₽" in r.text          # мелкая валюта — за 10/100/1000
    r = run(a.handle_text("Сколько стоит тенге"))
    assert "🇰🇿 Тенге — за 10: 1,65 ₽" in r.text
    r = run(a.handle_text("курс злотого"))
    assert r.text.startswith("🎩 Сэр, курса этой валюты у ЦБ нет!")


def test_currency_words_do_not_steal_normal_text():
    from alfred.brain.money import parse_currency as p
    assert p("Лев должен мне 500") is None                # имя, а не болгарский лев
    assert p("купил батон за 50") is None and p("сумма 300") is None
    assert p("индонезийская рупия") == "IDR" and p("египетский фунт") == "EGP"
    assert p("канадский доллар") == "CAD" and p("сомони") == "TJS" and p("100 сомов") == "KGS"
