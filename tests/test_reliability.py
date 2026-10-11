"""Тесты первой порции: ↩️ отмена последнего действия, защита от двойных сообщений, повторная отправка."""

import asyncio

import pytest

from alfred.core.security import SeenOnce

from .test_access import make, run


def owner_alfred(tmp_path):
    access, admin, llm = make(tmp_path)
    return access.alfred_for(access.owner), llm, access


# ---------------------------------------------------------------- ↩️ отмена

def test_undo_expense(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="CREATE_EXPENSE", amount_text="500", category="такси")
    run(a.handle_text("Потратил 500 на такси"))
    assert len(a.finance.latest()) == 1
    r = run(a.handle_text("Отмени последнее"))
    assert r.text == "🎩 Сэр, последняя операция отменена!\n\n↩️ Отменил: «Потратил 500 на такси»"
    assert a.finance.latest() == []
    assert run(a.handle_text("Отмени последнее")).text == "🎩 Сэр, отменять пока нечего!"


def test_undo_wrong_correction_keeps_earlier_data(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="CREATE_EXPENSE", amount_text="5000", category="продукты")
    run(a.handle_text("Потратил 5000 на продукты"))
    llm.said(intent="UPDATE_FINANCE", target="LAST", new_amount_text="7000")
    run(a.handle_text("Не 5000, а 7000"))
    assert a.finance.latest()[0].amount == 7000
    run(a.handle_text("Верни как было"))
    assert [o.amount for o in a.finance.latest()] == [5000]


def test_view_does_not_replace_undo_point(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить хлеб"))
    run(a.handle_text("📋 Ваши дела"))                    # просто посмотрел — точку отмены не трогает
    run(a.handle_text("Отмени последнее"))
    assert a.tasks.active() == []


def test_undo_button_press_and_deleted_note(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить хлеб"))
    t = a.tasks.active()[0]
    a.handle_callback(f"task:done:{t.id}")
    assert a.tasks.active() == []
    r = run(a.handle_text("/undo"))
    assert "кнопки" in r.text and [x.title for x in a.tasks.active()] == ["Купить хлеб"]
    llm.said(intent="CREATE_NOTE", content="Код домофона 1234")
    run(a.handle_text("Запиши код домофона 1234"))
    llm.said(intent="DELETE_NOTE", target="домофон")
    run(a.handle_text("Удали заметку про домофон"))
    assert a.notes.all() == []
    run(a.handle_text("Отмени это"))
    assert a.notes.all()[0].content == "Код домофона 1234"


def test_undo_only_own_data(tmp_path):
    a, llm, access = owner_alfred(tmp_path)
    access.users.add_invite("ivan_p")
    guest = access.alfred_for(access.who(555, "ivan_p", "Иван").account)
    llm.said(intent="CREATE_TASK", title="Моё дело")
    run(a.handle_text("Моё дело"))
    llm.said(intent="CREATE_TASK", title="Дело гостя")
    run(guest.handle_text("Дело гостя"))
    run(a.handle_text("Отмени последнее"))
    assert a.tasks.active() == [] and [t.title for t in guest.tasks.active()] == ["Дело гостя"]


def test_undo_keeps_what_happened_after_the_command(tmp_path):
    """«Отмени последнее» откатывает только последнюю команду. То, что случилось после неё само
    (бот отметил напоминание отправленным, ночью продлил расписание), остаётся как есть."""
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить хлеб"))
    llm.said(intent="CREATE_NOTE", content="Код 1234")
    run(a.handle_text("Запиши код 1234"))
    a.tasks.complete(a.tasks.active(), a.now())          # изменилось «само», без команды
    run(a.handle_text("Отмени последнее"))
    assert a.notes.all() == []                           # команда отменена
    assert a.tasks.active() == []                        # а то, что было после неё, не откатилось


def test_plain_cancel_hints_undo(tmp_path):
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить хлеб"))
    r = run(a.handle_text("Отмени"))
    assert "«Отмени последнее»" in r.text and len(a.tasks.active()) == 1


# ---------------------------------------------------------------- двойные сообщения

def test_seen_once():
    s = SeenOnce(size=3)
    assert s.first_time(("m", 1, 10)) and not s.first_time(("m", 1, 10))
    assert s.first_time(("m", 1, 11))                    # другое сообщение — обрабатываем
    for i in range(5):
        s.first_time(("x", i))
    assert s.first_time(("m", 1, 10))                    # старое уже забыто — память не растёт


# ---------------------------------------------------------------- повторная отправка

class FlakyBot:
    def __init__(self, fails):
        self.fails, self.calls = fails, 0

    async def send_message(self, chat_id, text, **kw):
        from aiogram.exceptions import TelegramNetworkError
        self.calls += 1
        if self.calls <= self.fails:
            raise TelegramNetworkError(method=None, message="timeout")
        return "ok"


def test_send_retries(monkeypatch):
    from alfred.handlers import telegram as tg

    async def no_sleep(_):
        return None
    monkeypatch.setattr(tg.asyncio, "sleep", no_sleep)
    bot = FlakyBot(fails=2)
    assert run(tg.send_with_retry(bot, 1, "hi")) == "ok" and bot.calls == 3
    bot = FlakyBot(fails=5)
    with pytest.raises(Exception):
        run(tg.send_with_retry(bot, 1, "hi"))
    assert bot.calls == 3


class RecordingBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw))
        return "ok"


def test_long_reply_is_split_buttons_under_last(monkeypatch):
    """Ответ длиннее лимита Telegram уходит несколькими сообщениями, ничего не теряется."""
    from alfred.core.reply import Reply
    from alfred.handlers import telegram as tg
    bot = RecordingBot()
    text = "🎩 Ваши заметки, Сэр!\n\n" + "\n".join(f"📝 Заметка {i} " + "слово " * 60 for i in range(30))
    run(tg.send_reply(bot, 1, Reply(text, buttons=[[("Ок", "x:1")]])))
    assert len(bot.sent) > 1
    assert all(len(t) <= 4096 for _, t, _ in bot.sent)
    words = lambda s: "".join(s.split())       # noqa: E731 — сравниваем без пробелов и переносов на стыках
    assert words("".join(t for _, t, _ in bot.sent)) == words(text)
    markups = [kw["reply_markup"] for _, _, kw in bot.sent]
    assert markups[:-1] == [None] * (len(markups) - 1) and markups[-1].inline_keyboard
    assert tg.split_text("x" * 9000) == ["x" * 4000, "x" * 4000, "x" * 1000]   # без переносов — режем по символам


def test_one_queue_per_user(tmp_path):
    _, _, access = owner_alfred(tmp_path)
    assert access.lock(1) is access.lock(1) and access.lock(1) is not access.lock(2)


def test_messages_of_one_user_wait_for_each_other(tmp_path):
    """Пока Альфред думает над первым сообщением, второе ждёт, а не перебивает его."""
    _, _, access = owner_alfred(tmp_path)
    order = []

    async def command(name, delay):
        async with access.lock(7):
            order.append(f"{name} начал")
            await asyncio.sleep(delay)
            order.append(f"{name} закончил")

    async def both():
        await asyncio.gather(command("первое", 0.05), command("второе", 0))
    run(both())
    assert order == ["первое начал", "первое закончил", "второе начал", "второе закончил"]


def test_unexpected_error_gets_polite_answer(tmp_path):
    """Непредвиденная ошибка: человек получает извинение, а не тишину; чужим — ничего."""
    from datetime import datetime
    from aiogram.types import Chat, ErrorEvent, Message, Update, User
    from alfred.handlers import telegram as tg
    from .test_access import OWNER
    access, admin, _ = make(tmp_path)
    router = tg.build_router(access, admin)
    on_error = router.errors.handlers[0].callback

    def event(user_id):
        msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=user_id, type="private"),
                      from_user=User(id=user_id, is_bot=False, first_name="A"), text="привет")
        return ErrorEvent(update=Update(update_id=1, message=msg), exception=RuntimeError("сбой"))

    bot = RecordingBot()
    run(on_error(event(OWNER), bot))
    assert bot.sent == [(OWNER, tg.T.SOMETHING_WRONG, {})]
    run(on_error(event(424242), bot))                      # незнакомец — молчим
    assert len(bot.sent) == 1


def test_med_reminder_requeued_after_failed_send(tmp_path):
    from datetime import date, datetime
    from .test_alfred import TZ
    a, llm, _ = owner_alfred(tmp_path)
    llm.said(intent="MED_CASE", med={"illness": "Ангина", "drugs": [{"name": "Амоксициллин", "per_day": 3,
                                                                      "days": 7}]})
    run(a.handle_text("Ангина, амоксициллин 3 раза в день 7 дней"))
    a._clock = lambda: datetime(2026, 9, 24, 14, 0, tzinfo=TZ)
    [r] = a.collect_med_reminders()
    a.med_send_failed(r)                                 # Telegram не ответил
    a._clock = lambda: datetime(2026, 9, 24, 14, 1, tzinfo=TZ)
    [again] = a.collect_med_reminders()                  # через минуту — снова
    assert "Амоксициллин" in again.text
