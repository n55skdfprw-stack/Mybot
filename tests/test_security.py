"""Тесты защиты: спам, чужие, длинные сообщения, приглашения, логи."""

import logging

from alfred.core.security import PER_BURST, PER_MINUTE, RateLimiter

from .test_access import make, run


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_burst_and_minute_limits():
    c = Clock()
    rl = RateLimiter(c)
    results = [rl.allow(1) for _ in range(PER_BURST)]
    assert all(ok for ok, _ in results)
    ok, warn = rl.allow(1)
    assert not ok and warn                               # предупредили один раз
    assert rl.allow(1) == (False, False)                 # дальше молча
    c.t += 5
    assert rl.allow(1)[0]                                # пауза — снова можно
    assert rl.allow(2)[0]                                # другие люди не страдают
    for _ in range(PER_MINUTE):
        c.t += 3.1
        rl.allow(3)
    assert not rl.allow(3)[0]
    c.t += 61
    assert rl.allow(3)[0]


def test_stranger_gets_one_reply_per_10_minutes():
    c = Clock()
    rl = RateLimiter(c)
    assert rl.stranger_may_get_reply(9)
    assert not rl.stranger_may_get_reply(9)
    c.t += 601
    assert rl.stranger_may_get_reply(9)


def test_invite_expires(tmp_path):
    access, admin, _ = make(tmp_path)
    admin.command("добавь @old_friend")
    with access.db.connect() as conn:
        conn.execute("UPDATE invites SET created_at='2020-01-01T00:00:00'")
    assert access.who(777, "old_friend", "X").kind == "stranger"
    assert access.users.invites() == []


def test_guest_cannot_touch_owner_records_by_id(tmp_path):
    """Кнопки с чужим номером записи ничего не делают."""
    access, admin, llm = make(tmp_path)
    admin.command("добавь @ivan_p")
    owner = access.alfred_for(access.owner)
    guest = access.alfred_for(access.who(555, "ivan_p", "Иван").account)
    llm.said(intent="CREATE_TASK", title="Секретное дело")
    run(owner.handle_text("Секретное дело"))
    task = owner.tasks.active()[0]
    guest.handle_callback(f"task:done:{task.id}")
    guest.handle_callback(f"task:drop:{task.id}")
    assert owner.tasks.active()[0].title == "Секретное дело"


def test_logs_do_not_contain_user_words(tmp_path, caplog):
    access, admin, llm = make(tmp_path)
    owner = access.alfred_for(access.owner)
    llm.said(intent="CREATE_NOTE", content="Пароль от почты qwerty")
    with caplog.at_level(logging.INFO):
        run(owner.handle_text("Пароль от почты qwerty"))
    assert "qwerty" not in caplog.text


def test_long_paste_split_by_telegram_answered_once():
    """Живая ошибка: огромный текст пришёл 4 кусками — 3 раза «слишком длинное» и хвост ушёл к ИИ."""
    c = Clock()
    rl = RateLimiter(c)
    assert rl.too_long(1, 4000, 1500) == (True, True)
    c.t += 0.5
    assert rl.too_long(1, 4000, 1500) == (True, False)
    c.t += 0.5
    assert rl.too_long(1, 900, 1500) == (True, False)        # хвостик этой же вставки
    c.t += 30
    assert rl.too_long(1, 50, 1500) == (False, False)        # новое обычное сообщение — как обычно
