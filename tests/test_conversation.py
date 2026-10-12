"""💬 Разговор «как с человеком»: память последних реплик, вопросы о своих записях, счётчик токенов.
Сегодня — среда, 23 сентября 2026, 11:00 (как в test_alfred)."""

from datetime import date, datetime, timedelta

from alfred.brain.money import parse_period
from alfred.core.tokens import TokenMeter

from .test_access import make as make_access
from .test_alfred import NOW, TZ, make, run


class Spy:
    """Запоминает, что Альфред отправил GigaChat."""

    def __init__(self, llm):
        self.sent = []
        orig = llm.complete

        async def spy(system, user, pro=False):
            self.sent.append((system, user))
            return await orig(system, user, pro)
        llm.complete = spy


# ---------------------------------------------------------------- память разговора

def test_recent_dialogue_goes_to_ai(tmp_path):
    a, llm, _ = make(tmp_path)
    spy = Spy(llm)
    llm.said(intent="CREATE_TASK", title="Купить молоко")
    run(a.handle_text("купи молоко"))
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("и хлеб ещё"))
    prompt = spy.sent[-1][1]
    assert "Недавний разговор (старое сверху):\nПользователь: Купи молоко\nАльфред: 🎩" in prompt
    assert "⭕" not in prompt                                   # от ответа — только первая строка, без самих записей
    assert prompt.endswith("Сообщение пользователя: «и хлеб ещё»\nВерни только JSON.")
    assert {t.title for t in a.tasks.active()} == {"Купить молоко", "Купить хлеб"}


def test_dialogue_is_forgotten_after_long_pause(tmp_path):
    a, llm, _ = make(tmp_path)
    spy = Spy(llm)
    llm.said(intent="CREATE_TASK", title="Купить молоко")
    run(a.handle_text("купи молоко"))
    a._clock = lambda: NOW + timedelta(minutes=45)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("и хлеб ещё"))
    assert "Недавний разговор" not in spy.sent[-1][1]


def test_dialogue_keeps_only_last_six(tmp_path):
    a, llm, _ = make(tmp_path)
    spy = Spy(llm)
    for i in range(8):
        llm.said(intent="CREATE_TASK", title=f"Дело {i}")
        run(a.handle_text(f"дело номер {i}"))
    prompt = spy.sent[-1][1].lower()
    assert "дело номер 6" in prompt and "дело номер 1" in prompt and "дело номер 0" not in prompt


def test_notes_are_not_repeated_in_dialogue(tmp_path):
    a, llm, _ = make(tmp_path)
    spy = Spy(llm)
    llm.said(intent="CREATE_NOTE", content="Код от сейфа 4711")
    run(a.handle_text("запиши код от сейфа 4711"))
    a._set_last("task", None)                                  # как в test_security: разговор ушёл от заметки
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("купи хлеб"))
    assert "4711" not in spy.sent[-1][1] and "(сообщение о заметке)" in spy.sent[-1][1]


# ---------------------------------------------------------------- вопросы о своих записях

def test_question_about_own_schedule_is_answered_from_records(tmp_path):
    a, llm, _ = make(tmp_path)
    spy = Spy(llm)
    a.schedule.create({"type": "lecture", "date": "2026-09-28", "start_time": "10:00", "end_time": "11:30",
                       "discipline": "Анатомия", "location": "Ауд. 305"}, NOW)
    a.tasks.create("Сдать реферат", date(2026, 9, 25))
    llm.said(intent="ASK", query="когда анатомия")
    llm.queue.append("Анатомия у вас в понедельник, 28 сентября, в 10:00 в Ауд. 305, **Сэр**.")
    r = run(a.handle_text("Когда у меня анатомия?"))
    assert r.text == "🎩 Анатомия у вас в понедельник, 28 сентября, в 10:00 в Ауд. 305, Сэр."   # без звёздочек
    system, facts = spy.sent[-1]
    assert "ТОЛЬКО по его записям" in system
    assert "пн 28.09, 10:00–11:30, лекция, «Анатомия», Ауд. 305" in facts
    assert "Сдать реферат (до 25.09)" in facts and facts.endswith("Вопрос: «Когда у меня анатомия?»")


def test_unclear_question_is_answered_from_records_instead_of_not_understood(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="UNKNOWN")
    llm.queue.append("В записях этого нет, Сэр.")
    r = run(a.handle_text("А когда у меня окно между парами?"))
    assert r.text == "🎩 В записях этого нет, Сэр."


def test_question_when_ai_is_down(tmp_path):
    from alfred.brain.llm_client import LLMError
    a, llm, _ = make(tmp_path)
    llm.said(intent="ASK", query="что-то")
    llm.queue.append(LLMError("нет связи"))
    assert "недоступен" in run(a.handle_text("Когда у меня анатомия?")).text


# ---------------------------------------------------------------- «понедельник» — не «неделя»

def test_monday_is_not_week(tmp_path):
    a, llm, _ = make(tmp_path)
    a.schedule.create({"type": "lecture", "date": "2026-09-28", "start_time": "10:00"}, NOW)
    llm.said(intent="SHOW_SCHEDULE", event_when="в понедельник")
    r = run(a.handle_text("а в понедельник?"))
    assert "на понедельник" in r.text.lower() and "10:00" in r.text
    llm.said(intent="SHOW_SCHEDULE", event_when="на неделю")
    assert "на этой неделе" in run(a.handle_text("что на неделе")).text.lower()


def test_money_period_monday_is_not_week():
    today = date(2026, 9, 23)
    assert parse_period("за неделю", today)[2] == "за неделю"
    assert parse_period("в понедельник", today) is None


# ---------------------------------------------------------------- 🧮 счётчик токенов

def test_token_meter_and_system_view(tmp_path):
    access, admin, llm = make_access(tmp_path)
    meter = access.tokens
    meter.add("Lite", 4500)
    meter.add("Lite", 4500)
    meter.add("Pro", 1_200_000)
    assert (meter.today("Lite"), meter.month("Pro")) == (9000, 1_200_000)
    lines = meter.lines()
    assert "сегодня: Lite 9 тыс · Pro 1,2 млн" in lines and "📈" in lines
    assert "🧮 Токены GigaChat" in run(admin.system_view()).text


def test_token_meter_counts_per_day(tmp_path):
    access, admin, llm = make_access(tmp_path)
    day = [datetime(2026, 9, 24, 12, 0, tzinfo=TZ)]
    meter = TokenMeter(access.users, lambda: day[0])
    meter.add("Lite", 100)
    day[0] += timedelta(days=1)
    meter.add("Lite", 50)
    assert meter.today("Lite") == 50 and meter.month("Lite") == 150
