"""Сквозные проверки «как через Telegram»: сообщение проходит весь путь — Telegram → очередь → Альфред → ответ.

Настоящий Telegram заменён «подставным»: он запоминает всё, что Альфред пытается отправить или изменить.
Настоящий GigaChat — тоже подставной (отвечает заранее заданным JSON). Живую речь проверяет tools/live_check.py.
"""

import asyncio
from datetime import date, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from alfred.handlers.telegram import build_dispatcher
from alfred.ui import texts as T

from .test_access import OWNER, Checker, make, run


class SlowLLM(Checker):
    """GigaChat, который думает: пока он отвечает, может прийти следующее сообщение."""
    delay = 0.05

    async def complete(self, system, user, pro=False):
        await asyncio.sleep(self.delay)
        return await super().complete(system, user, pro)


class FakeTelegram(BaseSession):
    """Подставной Telegram: ничего никуда не шлёт, только записывает."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self._next_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name == "SendMessage":
            self._next_id += 1
            return Message(message_id=self._next_id, date=datetime.now(), text=method.text,
                           chat=Chat(id=method.chat_id, type="private"))
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass

    def sent(self) -> list[str]:
        return [m.text for m in self.calls if type(m).__name__ == "SendMessage"]

    def edited(self) -> list[str]:
        return [m.text for m in self.calls if type(m).__name__ == "EditMessageText"]

    def of(self, name: str) -> list:
        return [m for m in self.calls if type(m).__name__ == name]


class Chatroom:
    """Альфред целиком (как на Railway), но с подставными Telegram и GigaChat."""

    def __init__(self, tmp_path, slow=False):
        self.access, self.admin, llm = make(tmp_path)
        if slow:
            self.access.brain.llm = llm = SlowLLM()
        self.llm = llm
        self.tg = FakeTelegram()
        self.bot = Bot(token="42:TEST", session=self.tg)
        self.dp = build_dispatcher(self.access, self.admin)
        self._msg_id = 0
        self._upd_id = 0

    def _ids(self):
        self._msg_id += 1
        self._upd_id += 1
        return self._msg_id, self._upd_id

    def message(self, text: str, user_id: int = OWNER, username: str = "owner") -> Update:
        mid, uid = self._ids()
        msg = Message(message_id=mid, date=datetime.now(), text=text, chat=Chat(id=user_id, type="private"),
                      from_user=User(id=user_id, is_bot=False, first_name="Тест", username=username))
        return Update(update_id=uid, message=msg)

    def press(self, data: str, user_id: int = OWNER) -> Update:
        mid, uid = self._ids()
        user = User(id=user_id, is_bot=False, first_name="Тест")
        source = Message(message_id=mid, date=datetime.now(), text="…", chat=Chat(id=user_id, type="private"))
        return Update(update_id=uid, callback_query=CallbackQuery(id=str(uid), from_user=user, chat_instance="c",
                                                                  data=data, message=source))

    async def feed(self, *updates: Update):
        """Отдать Альфреду одно или несколько обновлений — несколько приходят одновременно, как в жизни."""
        await asyncio.gather(*(self.dp.feed_update(self.bot, u) for u in updates))

    def say(self, text: str, **ai):
        if ai:
            self.llm.said(**ai)
        run(self.feed(self.message(text)))

    def buttons(self) -> list[str]:
        """callback_data всех кнопок последнего отправленного сообщения."""
        markup = self.tg.of("SendMessage")[-1].reply_markup
        return [b.callback_data for row in getattr(markup, "inline_keyboard", []) for b in row]

    @property
    def alfred(self):
        return self.access.alfred_for(self.access.owner)


# ---------------------------------------------------------------- обычный разговор

def test_message_goes_all_the_way(tmp_path):
    room = Chatroom(tmp_path)
    room.say("Купить молоко", intent="CREATE_TASK", title="Купить молоко")
    assert any("Купить молоко" in t for t in room.tg.sent())
    assert room.tg.of("SendChatAction")                      # «Альфред печатает…»
    assert [t.title for t in room.alfred.tasks.active()] == ["Купить молоко"]


def test_stranger_gets_polite_refusal_without_ai(tmp_path):
    room = Chatroom(tmp_path)
    run(room.feed(room.message("Привет, запиши дело", user_id=777, username="chuzhoi")))
    assert room.tg.sent() == [T.INVITE_ONLY]                 # GigaChat не вызывался — иначе тест бы упал


def test_same_message_twice_is_handled_once(tmp_path):
    room = Chatroom(tmp_path)
    room.llm.said(intent="CREATE_EXPENSE", amount_text="500", category="такси")
    upd = room.message("Потратил 500 на такси")
    run(room.feed(upd))
    run(room.feed(upd))                                      # Telegram прислал то же сообщение повторно
    assert len(room.alfred.finance.latest()) == 1


# ---------------------------------------------------------------- очередь сообщений

def test_two_quick_messages_do_not_mix(tmp_path):
    """Два сообщения подряд, не дожидаясь ответа: у первого дела дата не теряется."""
    room = Chatroom(tmp_path, slow=True)
    room.llm.said(intent="CREATE_TASK", title="Купить молоко", due_when="завтра")
    room.llm.said(intent="CREATE_TASK", title="Позвонить маме")
    run(room.feed(room.message("Купить молоко завтра"), room.message("Позвонить маме")))
    tasks = {t.title: t.due_date for t in room.alfred.tasks.active()}
    assert tasks == {"Купить молоко": date(2026, 9, 25), "Позвонить маме": None}


def test_undo_after_two_quick_messages_cancels_only_last(tmp_path):
    room = Chatroom(tmp_path, slow=True)
    room.llm.said(intent="CREATE_TASK", title="Купить молоко")
    room.llm.said(intent="CREATE_TASK", title="Позвонить маме")
    run(room.feed(room.message("Купить молоко"), room.message("Позвонить маме")))
    room.say("Отмени последнее")
    assert [t.title for t in room.alfred.tasks.active()] == ["Купить молоко"]


# ---------------------------------------------------------------- длинные ответы

def test_long_notes_list_arrives_in_parts(tmp_path):
    room = Chatroom(tmp_path)
    for i in range(10):
        room.alfred.notes.create(f"Заметка {i}: " + "очень длинный текст " * 30)
    room.say(T.MENU_NOTES)
    sent = room.tg.sent()
    assert len(sent) >= 2 and all(len(t) <= 4096 for t in sent)
    assert "Заметка 0" in "".join(sent) and "Заметка 9" in "".join(sent)


# ---------------------------------------------------------------- кнопки

def test_button_press_answers_and_updates_message(tmp_path):
    room = Chatroom(tmp_path)
    task = room.alfred.tasks.create("Купить хлеб", None)
    run(room.feed(room.press(f"task:done:{task.id}")))
    assert room.tg.of("AnswerCallbackQuery")                 # «часики» на кнопке убраны
    assert room.tg.of("EditMessageText")                     # список обновился на месте
    assert room.alfred.tasks.active() == []


def test_old_choice_button_does_not_touch_new_question(tmp_path):
    room = Chatroom(tmp_path)
    for title in ("Позвонить Сергею", "Написать Сергею"):
        room.alfred.tasks.create(title, None)
    for text in ("Сергей любит чай", "Сергей живёт в Туле"):
        room.alfred.notes.create(text)
    room.say("Удали дело про Сергея", intent="DELETE_TASK", target="Сергею")
    old = room.buttons()[0]
    room.say("Удали заметку про Сергея", intent="DELETE_NOTE", target="Сергей")
    run(room.feed(room.press(old)))
    assert any("неактуален" in t for t in room.tg.edited())
    assert len(room.alfred.tasks.active()) == 2 and len(room.alfred.notes.all()) == 2


# ---------------------------------------------------------------- ошибки

def test_unexpected_error_gets_apology_not_silence(tmp_path, monkeypatch):
    room = Chatroom(tmp_path)

    async def broken(text):
        raise RuntimeError("что-то сломалось внутри")
    monkeypatch.setattr(room.alfred, "handle_text", broken)
    room.say("Купить хлеб")
    assert room.tg.sent()[-1] == T.SOMETHING_WRONG


def test_error_on_button_removes_spinner(tmp_path, monkeypatch):
    room = Chatroom(tmp_path)

    async def broken(data):
        raise RuntimeError("сломалась кнопка")
    monkeypatch.setattr(room.alfred, "handle_callback_async", broken)
    run(room.feed(room.press("task:list")))
    assert room.tg.of("AnswerCallbackQuery") and room.tg.sent()[-1] == T.SOMETHING_WRONG


def test_ai_unavailable_is_reported(tmp_path):
    from alfred.brain.llm_client import LLMError
    room = Chatroom(tmp_path)
    room.llm.queue += [LLMError("нет связи"), LLMError("нет связи")]
    room.say("Запиши что-нибудь")
    assert room.tg.sent()[-1] == T.AI_UNAVAILABLE
