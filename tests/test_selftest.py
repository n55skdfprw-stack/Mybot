"""🧪 Самопроверка: прогон живых фраз на временной базе, отчёт владельцу, запуск после обновления."""

import asyncio
import json

from alfred import VERSION
from alfred.brain.brain import Brain
from alfred.brain.llm_client import LLMError
from alfred.core import selftest
from alfred.core.selftest import STEPS, after_update, run_selftest
from alfred.services.weather import FORECAST_URL, GEO_URL, WeatherService

from .test_access import OWNER, Checker, make as make_access, run
from .test_telegram_flow import Chatroom
from .test_weather import CITIES, forecast_json


class IdealLLM(Checker):
    """GigaChat, который понимает каждую фразу самопроверки идеально (кроме тех, что попросили «не понять»)."""

    def __init__(self, misunderstand: tuple = (), down: bool = False):
        super().__init__()
        self.answers = {s.say: s.ideal for s in STEPS if s.ideal}
        self.misunderstand, self.down = misunderstand, down

    async def complete(self, system, user, pro=False):
        if self.down:
            raise LLMError("GigaChat недоступен")
        said = user.split("Сообщение пользователя: «", 1)[-1].split("»", 1)[0]
        if said in self.misunderstand or said not in self.answers:
            return json.dumps({"intent": "UNKNOWN"})
        return json.dumps(self.answers[said], ensure_ascii=False)


def make(tmp_path, **llm):
    access, admin, _ = make_access(tmp_path)
    access.brain = Brain(IdealLLM(**llm))

    async def fetch(url, params):
        if url == GEO_URL:
            found = CITIES.get(params["name"].lower())
            return {"results": [found]} if found else {}
        assert url == FORECAST_URL
        return forecast_json()
    access.weather = WeatherService(fetch=fetch)
    return access, admin


def test_all_steps_pass_when_ai_understands(tmp_path):
    """Если GigaChat всё понимает правильно — все проверки зелёные (значит, сами проверки написаны верно)."""
    access, _ = make(tmp_path)
    r = run(run_selftest(access))
    assert f"✅ {len(STEPS)} из {len(STEPS)} — всё работает!" in r.text, r.text
    assert r.text.startswith(f"🎩 Самопроверка версии {VERSION}, Сэр!")


def test_misunderstood_phrase_is_reported(tmp_path):
    access, _ = make(tmp_path, misunderstand=("Потратил 350 рублей на такси",))
    r = run(run_selftest(access))
    assert f"⚠️ {len(STEPS) - 1} из {len(STEPS)} — есть проблемы!" in r.text
    assert "❌ Расход\n   фраза: «Потратил 350 рублей на такси»" in r.text
    assert "✅ Работает:" in r.text and "Дело с датой" in r.text


def test_ai_down_is_reported_not_crash(tmp_path):
    access, _ = make(tmp_path, down=True)
    r = run(run_selftest(access))
    assert "есть проблемы" in r.text and "Альфред ответил, что не смог" in r.text


def test_real_data_is_not_touched(tmp_path):
    access, _ = make(tmp_path)
    owner = access.alfred_for(access.owner)
    owner.notes.create("Моя настоящая заметка")
    run(run_selftest(access))
    assert [n.content for n in owner.notes.all()] == ["Моя настоящая заметка"]
    assert owner.tasks.active() == [] and owner.finance.latest() == []      # проверочные записи — не здесь


def test_runs_once_after_each_update(tmp_path):
    access, _ = make(tmp_path)
    got = []

    async def send(reply):
        got.append(reply.text)
    run(after_update(access, send, delay=0))
    assert len(got) == 1 and "всё работает" in got[0]
    run(after_update(access, send, delay=0))                   # перезапуск той же версии — не повторяем
    assert len(got) == 1
    access.users.set_setting("selftest_version", "0.0")       # «вышла новая версия»
    run(after_update(access, send, delay=0))
    assert len(got) == 2


def test_selftest_command_owner_only(tmp_path):
    room = Chatroom(tmp_path)
    room.access.brain = Brain(IdealLLM())

    async def go(update):
        await room.feed(update)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending)                           # самопроверка идёт в фоне — дожидаемся

    room.access.users.add_invite("ivan_p")
    guest = room.access.who(555, "ivan_p", "Иван").account
    room.access.users.set_address(guest.id, "Сэр")
    run(go(room.message("/selftest", user_id=555, username="ivan_p")))
    assert room.tg.sent()[-1] == "🎩 Сэр, эта команда — только для хозяина Альфреда!"
    run(go(room.message("/selftest")))
    texts = room.tg.sent()
    assert texts[-2] == "🎩 Запускаю самопроверку, Сэр! Это займёт 1–2 минуты."
    assert texts[-1].startswith(f"🎩 Самопроверка версии {VERSION}, Сэр!")


def test_system_panel_has_selftest_button(tmp_path):
    access, admin = make(tmp_path)
    r = run(admin.system_view())
    assert [("🧪 Самопроверка", "adm:selftest")] in r.buttons
    assert selftest._running is False
