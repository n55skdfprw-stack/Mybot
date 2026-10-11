"""Часовые пояса: у каждого — своё местное время («Я в Токио»), сводки и проверки приходят по нему."""

from zoneinfo import ZoneInfo

from alfred.notifications.scheduler import daily_tick

from .test_access import OWNER, make as make_access, run
from .test_weather import make as make_weather, say


def test_moving_to_another_city_moves_the_clock(tmp_path):
    a, llm, _ = make_weather(tmp_path)
    moscow_hour = a.now().hour
    r = say(a, llm, "Я в Токио", intent="SET_CITY", city="Токио")
    assert "🕐 Перевёл часы на местное время" in r.text
    assert a.tz == ZoneInfo("Asia/Tokyo") and a.now().hour == (moscow_hour + 6) % 24
    assert a.users.by_id(a.user_id).timezone == "Asia/Tokyo"            # запомнил — переживёт перезапуск
    r = say(a, llm, "Я дома", intent="SET_CITY", city="HOME")
    assert "🕐 Перевёл часы" in r.text and a.tz == ZoneInfo("Europe/Moscow")


def test_same_timezone_city_does_not_mention_clock(tmp_path):
    a, llm, _ = make_weather(tmp_path)
    r = say(a, llm, "Я в Казани", intent="SET_CITY", city="Казань")
    assert "🕐" not in r.text


def test_daily_check_comes_at_local_time_once(tmp_path):
    """В Москве 12:00, в Екатеринбурге 14:00: дневная проверка дел приходит гостю из Екатеринбурга
    (и только один раз), а владельцу в Москве — пока нет."""
    access, admin, llm = make_access(tmp_path)
    access.users.add_invite("ivan_p")
    guest = access.who(555, "ivan_p", "Иван").account
    access.users.set_timezone(guest.id, "Asia/Yekaterinburg")
    for acc in (access.owner, access.users.by_id(guest.id)):
        a = access.alfred_for(acc)
        a.tasks.create("Позвонить врачу", a.today())
    sent = []

    async def send_to(acc, reply):
        sent.append((acc.telegram_id, reply.text))

    run(daily_tick(access, send_to))
    assert [who for who, _ in sent] == [555] and "внимания" in sent[0][1]
    run(daily_tick(access, send_to))                     # следующая минута — второй раз не шлём
    assert len(sent) == 1
    assert OWNER not in [who for who, _ in sent]


def test_unknown_timezone_falls_back_to_moscow(tmp_path):
    access, admin, llm = make_access(tmp_path)
    access.users.set_timezone(access.owner.id, "Марс/Олимп")
    assert access.alfred_for(access.owner).tz == ZoneInfo("Europe/Moscow")
