"""Тесты восьмого этапа — режимы доступа: 👑 владелец и 👤 приглашённые."""

import asyncio
from datetime import datetime

from alfred.brain.brain import Brain
from alfred.core.access import Access
from alfred.core.admin import Admin
from alfred.database.db import Database
from alfred.services.currency import CurrencyService
from alfred.services.weather import WeatherService

from .test_alfred import TZ, FakeLLM, style_ok
from .test_weather import forecast_json

OWNER = 1000


def run(coro):
    return asyncio.run(coro)


class Checker(FakeLLM):
    async def check(self):
        return True


def make(tmp_path, limit=3):
    db = Database(str(tmp_path / "a.db"))
    db.migrate()
    llm = Checker()

    async def cbr():
        from .test_finance import CBR
        return CBR

    async def wx(url, params):
        return forecast_json()

    access = Access(db, Brain(llm), OWNER, TZ, "Санкт-Петербург", currency=CurrencyService(fetch=cbr),
                    weather=WeatherService(fetch=wx), default_limit=limit,
                    clock=lambda: datetime(2026, 9, 24, 12, 0, tzinfo=TZ))
    access.setup_owner()
    return access, Admin(access, llm), llm


def test_stranger_is_not_let_in(tmp_path):
    access, admin, _ = make(tmp_path)
    assert access.who(555, "ivan", "Иван").kind == "stranger"
    assert access.users.by_telegram(555) is None


def test_invite_join_and_separate_data(tmp_path):
    access, admin, llm = make(tmp_path)
    r = admin.command("Альфред, добавь @Ivan_Petrov")
    style_ok(r.text)
    assert r.text.startswith("🎩 Приглашение создано, Сэр!\n\n⏳ @ivan_petrov")
    assert access.who(555, "petr", "Пётр").kind == "stranger"          # чужой не пройдёт
    v = access.who(555, "Ivan_Petrov", "Иван")
    assert v.kind == "new" and v.account.role == "user"
    assert access.who(555, "ivan_petrov", "Иван").kind == "user"       # второй раз — обычный вход
    owner = access.alfred_for(access.owner)
    guest = access.alfred_for(v.account)
    llm.said(intent="CREATE_TASK", title="Купить молоко")
    run(owner.handle_text("Купить молоко"))
    llm.said(intent="CREATE_TASK", title="Сдать отчёт")
    run(guest.handle_text("Сдать отчёт"))
    assert [t.title for t in owner.tasks.active()] == ["Купить молоко"]
    assert [t.title for t in guest.tasks.active()] == ["Сдать отчёт"]


def test_daily_limit_for_guest_only(tmp_path):
    access, admin, llm = make(tmp_path, limit=2)
    admin.command("добавь @ivan_p")
    guest = access.alfred_for(access.who(555, "ivan_p", "Иван").account)
    owner = access.alfred_for(access.owner)
    for _ in range(2):
        llm.said(intent="CREATE_NOTE", content="Идея")
        run(guest.handle_text("Идея"))
    r = run(guest.handle_text("Ещё идея"))
    assert r.text.startswith("🎩 Прошу прощения, Сэр! На сегодня лимит сообщений исчерпан (2).")
    assert run(guest.handle_text("📋 Ваши дела")).text              # кнопки меню работают
    for _ in range(5):
        llm.said(intent="CREATE_NOTE", content="Моя идея")
        assert "лимит" not in run(owner.handle_text("Моя идея")).text
    r = admin.command("лимит для @ivan_p 10")
    assert r.text == "🎩 Готово, Сэр!\n\n🤖 Лимит для @ivan_p: 10 сообщений в день"
    llm.said(intent="CREATE_NOTE", content="Идея")
    assert "лимит" not in run(guest.handle_text("Идея")).text


def test_block_keeps_data_and_unblock(tmp_path):
    access, admin, llm = make(tmp_path)
    admin.command("добавь @ivan_p")
    acc = access.who(555, "ivan_p", "Иван").account
    guest = access.alfred_for(acc)
    llm.said(intent="CREATE_TASK", title="Сдать отчёт")
    run(guest.handle_text("Сдать отчёт"))
    r = admin.command("Закрой доступ @ivan_p")
    assert r.text == "🎩 Готово, Сэр! Доступ закрыт, данные сохранены.\n\n⛔ @ivan_p"
    assert access.who(555, "ivan_p", "Иван").kind == "blocked"
    assert [a.id for a, _ in access.active()] == [access.owner.id]   # закрытым не шлём напоминания
    admin.command("Открой доступ @ivan_p")
    assert access.who(555, "ivan_p", "Иван").kind == "user"
    assert [t.title for t in access.alfred_for(acc).tasks.active()] == ["Сдать отчёт"]


def test_delete_with_data(tmp_path):
    access, admin, llm = make(tmp_path)
    admin.command("добавь @ivan_p")
    acc = access.who(555, "ivan_p", "Иван").account
    guest = access.alfred_for(acc)
    llm.said(intent="CREATE_TASK", title="Сдать отчёт")
    run(guest.handle_text("Сдать отчёт"))
    r = admin.command("Удали @ivan_p")
    assert r.text.startswith("🎩 Сэр, удалить @ivan_p и ВСЕ его данные?")
    r = run(admin.callback(r.buttons[0][0][1]))
    assert r.text == "🎩 Готово, Сэр! @ivan_p удалён вместе с данными."
    assert access.users.by_telegram(555) is None
    assert access.who(555, "ivan_p", "Иван").kind == "stranger"       # повторно — только новым приглашением


def test_users_panel_and_card(tmp_path):
    access, admin, llm = make(tmp_path)
    access.who(OWNER, "d_owner", "D")
    admin.command("добавь @ivan_p")
    admin.command("добавь @masha_k")
    acc = access.who(555, "ivan_p", "Иван").account
    llm.said(intent="CREATE_NOTE", content="Идея")
    run(access.alfred_for(acc).handle_text("Идея"))
    r = admin.command("👥 Пользователи")
    style_ok(r.text)
    assert "👑 @d_owner — вы" in r.text
    assert "👤 @ivan_p · сегодня 1/3 · был сегодня в" in r.text
    assert "⏳ @masha_k · приглашён, ещё не заходил" in r.text
    card = run(admin.callback(r.buttons[0][0][1]))
    assert "🤖 Сообщений ИИ: сегодня 1 из 3 · всего 1" in card.text
    card = run(admin.callback(card.buttons[0][1][1]))           # ➕ Лимит +10
    assert "сегодня 1 из 13" in card.text
    r = run(admin.callback("adm:uninv:masha_k"))
    assert "masha_k" not in r.text


def test_system_view(tmp_path):
    access, admin, _ = make(tmp_path)
    r = run(admin.system_view())
    style_ok(r.text)
    assert "🤖 ИИ (GigaChat): ✅" in r.text and "💱 Курсы ЦБ: ✅" in r.text and "🌤 Погода: ✅" in r.text


def test_guest_cannot_use_owner_commands(tmp_path):
    """Команды владельца разбираются только для владельца — у гостя их просто нет в обработке."""
    access, admin, llm = make(tmp_path)
    admin.command("добавь @ivan_p")
    guest = access.alfred_for(access.who(555, "ivan_p", "Иван").account)
    llm.said(intent="UNKNOWN")
    r = run(guest.handle_text("Добавь @hacker"))
    assert access.users.invites() == []


# ---------------------------------------------------------------- 8.1: «Сэр» или «Мэм»

def test_address_mam_everywhere(tmp_path):
    from alfred.core.reply import Reply
    from alfred.ui.address import asked_address, personalize
    r = Reply("🎩 Записал, Сэр!", buttons=[[("Сэр, да", "x")]], extra=[Reply("🎩 Доброе утро, Сэр!")])
    m = personalize(r, "Мэм")
    assert m.text == "🎩 Записал, Мэм!" and m.buttons == [[("Мэм, да", "x")]]
    assert m.extra[0].text == "🎩 Доброе утро, Мэм!"
    assert personalize(r, "Сэр") is r and personalize(r, None) is r
    assert asked_address("Альфред, обращайся ко мне Мэм") == "Мэм"
    assert asked_address("Зови меня сэр") == "Сэр"
    assert asked_address("Купить молоко") is None


def test_address_saved(tmp_path):
    access, admin, _ = make(tmp_path)
    admin.command("добавь @olga_k")
    acc = access.who(777, "olga_k", "Ольга").account
    assert acc.address is None                      # спросим при первом знакомстве
    access.users.set_address(acc.id, "Мэм")
    assert access.who(777, "olga_k", "Ольга").account.address == "Мэм"


def test_address_phrases_live():
    """Живая ошибка: «Обращайся мэм» и «Обращайся сэр» не срабатывали."""
    from alfred.ui.address import asked_address
    for text, want in [("Обращайся мэм", "Мэм"), ("Обращайся сэр", "Сэр"), ("Называй меня Мэм", "Мэм"),
                       ("Обращайся ко мне Сэр", "Сэр"), ("Обращайтесь ко мне как «Мэм»", "Мэм")]:
        assert asked_address(text) == want, text


def test_invite_has_cancel_button(tmp_path):
    access, admin, _ = make(tmp_path)
    r = admin.command("Добавь @test_proverka")
    assert r.buttons[0] == [("❌ Отменить приглашение @test_proverka", "adm:uninv:test_proverka")]
    run(admin.callback("adm:uninv:test_proverka"))
    assert access.users.invites() == []
