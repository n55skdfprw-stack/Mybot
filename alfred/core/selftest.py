"""🧪 Самопроверка: Альфред сам прогоняет живые фразы через настоящий GigaChat, погоду и курсы ЦБ.

Запускается сама после каждого обновления (один раз на версию) и по команде владельца /selftest.
Работает на временной пустой базе — настоящие записи владельца и гостей не трогает и не видит.
Проверяет не только ответ, но и то, что на самом деле записалось (дата дела, сумма, время события…).
"""

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from .. import VERSION
from ..database.db import Database
from ..services.university import UniversityError
from ..ui import texts as T
from .access import Access
from .alfred import Alfred
from .reply import Reply
from .university import ERRORS as UNI_ERRORS

log = logging.getLogger(__name__)

STEP_TIMEOUT = 60          # секунд на одну фразу
FAILURES = (T.AI_UNAVAILABLE, T.NOT_UNDERSTOOD, T.DB_ERROR, T.SOMETHING_WRONG)
FAKE_OWNER = -1            # «владелец» временной базы — не настоящий человек


@dataclass
class Step:
    say: str
    about: str
    check: Callable[[Alfred, str], Optional[str]]    # None — всё хорошо, иначе — что не так
    ideal: Optional[dict] = field(default=None)       # как фразу должен понять GigaChat (для тестов самой проверки)
    ideal_answer: Optional[str] = None                # для вопросов (ASK) — каким мог бы быть ответ (для тестов)


def _day(a: Alfred, shift: int) -> date:
    return a.today() + timedelta(days=shift)


def _task_due(word: str, shift: int):
    def check(a: Alfred, _r: str) -> Optional[str]:
        found = [t for t in a.tasks.active() if word in t.title.lower()]
        if not found:
            return f"дела «{word}» нет в списке"
        if found[0].due_date != _day(a, shift):
            return f"у дела дата {found[0].due_date or 'не стоит'}, а должна быть {_day(a, shift)}"
        return None
    return check


def _task_exists(word: str):
    return lambda a, _r: None if any(word in t.title.lower() for t in a.tasks.active()) \
        else f"дела «{word}» нет в списке"


def _no_task(word: str):
    return lambda a, _r: f"дело «{word}» всё ещё в списке" if any(word in t.title.lower() for t in a.tasks.active()) \
        else None


def _note(word: str, present: bool):
    def check(a: Alfred, _r: str) -> Optional[str]:
        has = any(word in n.content for n in a.notes.all())
        if has != present:
            return "заметка не записалась" if present else "заметка не отменилась"
        return None
    return check


def _op(kind: str, amount: float, category: Optional[str] = None):
    def check(a: Alfred, _r: str) -> Optional[str]:
        ops = a.finance.latest(kind, 1)
        if not ops or abs(ops[0].amount - amount) > 0.01:
            return f"{'расход' if kind == 'expense' else 'доход'} {amount:g} ₽ не записался"
        if category and category not in (ops[0].category or "").lower():
            return f"категория «{ops[0].category}», а ожидалась «{category}»"
        return None
    return check


def _debt(amount: float):
    def check(a: Alfred, _r: str) -> Optional[str]:
        ok = any(d.direction == "owes_me" and abs(d.amount - amount) < 0.01 for _p, d in a.debts.active())
        return None if ok else f"долг {amount:g} ₽ «мне должны» не записался"
    return check


def _events_at(hhmm: str, days: int, at_least: int):
    def check(a: Alfred, _r: str) -> Optional[str]:
        found = [e for e in a.schedule.between(a.today(), _day(a, days)) if e.start_time == hhmm]
        return None if len(found) >= at_least else f"в распорядке {len(found)} событий в {hhmm}, нужно {at_least}+"
    return check


def _birthday(day: int, month: int):
    def check(a: Alfred, _r: str) -> Optional[str]:
        ok = any(b.day == day and b.month == month for b in a.birthdays.all(a.today()))
        return None if ok else f"дня рождения {day}.{month:02d} нет"
    return check


def _dossier(word: str):
    def check(a: Alfred, _r: str) -> Optional[str]:
        ok = any(word in " ".join(str(v) for v in vars(c).values()).lower() for c in a.dossier.all())
        return None if ok else f"в досье нет «{word}»"
    return check


def _med(per_day: int):
    def check(a: Alfred, _r: str) -> Optional[str]:
        drugs = [d for c in a.med.cases() for d in c.drugs]
        if not drugs:
            return "в медкарте нет лекарства"
        return None if drugs[0].per_day == per_day else f"{drugs[0].per_day} раз в день, а нужно {per_day}"
    return check


def _says(*words: str):
    return lambda _a, r: None if any(w.lower() in r.lower() for w in words) else "в ответе нет: " + " / ".join(words)


STEPS = [
    Step("Купить молоко завтра", "Дело с датой", _task_due("молок", 1),
         {"intent": "CREATE_TASK", "title": "Купить молоко", "due_when": "завтра"}),
    Step("и хлеб ещё", "💬 Продолжение: «и хлеб ещё»", _task_exists("хлеб"),
         {"intent": "CREATE_TASK", "title": "Купить хлеб"}),
    Step("Перенеси молоко на послезавтра", "Перенос дела", _task_due("молок", 2),
         {"intent": "UPDATE_TASK", "target": "молоко", "new_due_when": "на послезавтра"}),
    Step("Вычеркни молоко", "Дело выполнено", _no_task("молок"),
         {"intent": "COMPLETE_TASK", "target": "молоко"}),
    Step("Запиши заметку: код от сейфа 4711", "Заметка", _note("4711", True),
         {"intent": "CREATE_NOTE", "content": "Код от сейфа 4711"}),
    Step("Отмени последнее", "↩️ Отмена последнего", _note("4711", False)),
    Step("Потратил 350 рублей на такси", "Расход", _op("expense", 350, "такси"),
         {"intent": "CREATE_EXPENSE", "amount_text": "350", "category": "такси"}),
    Step("Сколько я потратил на такси?", "Вопрос о тратах", _says("350"),
         {"intent": "SEARCH_FINANCE", "category": "такси"}),
    Step("Получил зарплату 50000", "Доход", _op("income", 50000),
         {"intent": "CREATE_INCOME", "amount_text": "50000", "category": "зарплата"}),
    Step("Иван Роботов должен мне 1200", "Долг", _debt(1200),
         {"intent": "CREATE_DEBT", "person": "Иван Роботов", "direction": "owes_me", "amount_text": "1200"}),
    Step("В пятницу в 18:00 тренировка", "Событие в распорядке", _events_at("18:00", 8, 1),
         {"intent": "CREATE_EVENT", "event_type": "training", "event_when": "в пятницу", "time_text": "в 18:00"}),
    Step("Каждый понедельник в 10:00 лекция по анатомии", "Повторяющееся событие", _events_at("10:00", 15, 2),
         {"intent": "CREATE_EVENT", "event_type": "lecture", "discipline": "Анатомия", "time_text": "в 10:00",
          "repeat_text": "каждый понедельник"}),
    Step("Что у меня в пятницу?", "Распорядок на день", _says("18:00"),
         {"intent": "SHOW_SCHEDULE", "event_when": "в пятницу"}),
    Step("а в понедельник?", "💬 Продолжение: «а в понедельник?»", _says("10:00"),
         {"intent": "SHOW_SCHEDULE", "event_when": "в понедельник"}),
    Step("Когда у меня анатомия?", "💬 Вопрос о своих записях", _says("10:00"),
         {"intent": "ASK", "query": "когда занятия по анатомии"},
         ideal_answer="🎩 Анатомия у вас по понедельникам в 10:00, Сэр."),
    Step("У Ивана Роботова день рождения 12 марта", "День рождения", _birthday(12, 3),
         {"intent": "CREATE_BIRTHDAY", "person": "Иван Роботов", "bday_text": "12 марта"}),
    Step("Иван Роботов любит пионы", "Досье", _dossier("пион"),       # Иван уже известен — по долгу
         {"intent": "UPDATE_PERSON", "person": "Иван Роботов", "dossier": {"likes": "пионы"}}),
    Step("Заболел ангиной, врач назначил амоксициллин 500 мг 3 раза в день 7 дней", "Медкарта", _med(3),
         {"intent": "MED_CASE", "med": {"illness": "Ангина", "doctor": "врач", "drugs": [
             {"name": "Амоксициллин", "dose": "500 мг", "per_day": 3, "days": 7}]}}),
    Step("Что у меня про Ивана Роботова?", "Поиск по всем разделам", _says("Роботов")),
    Step("Сколько 100 долларов в рублях?", "Курс ЦБ и пересчёт", _says("₽"),
         {"intent": "CONVERT_CURRENCY", "amount_text": "100", "currency": "доллары", "convert_to": "рубли"}),
    Step("Погода на завтра", "Погода", _says("°"), {"intent": "SHOW_WEATHER"}),
    Step("Сколько сейчас времени в Токио?", "Время в другом городе", _says("🕐 Токио"),
         {"intent": "TIME_IN_CITY", "city": "Токио"}),
]

_running = False


async def run_selftest(access: Access, steps: list[Step] = STEPS) -> Reply:
    """Прогнать все фразы на временной базе и вернуть отчёт для владельца."""
    global _running
    if _running:
        return Reply("🎩 Сэр, самопроверка уже идёт — пришлю итог, как только закончу!")
    _running = True
    started = time.monotonic()
    results: list[tuple[Step, Optional[str], str]] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "selftest.db"))
            db.migrate()
            probe = Access(db, access.brain, FAKE_OWNER, access.tz, access.city,
                           currency=access.currency, weather=access.weather, clock=access.clock)
            alfred = probe.alfred_for(probe.setup_owner())
            for step in steps:
                results.append(await _run_step(alfred, step))
                alfred._clear_pending()                    # незаконченный вопрос не должен съесть следующую фразу
            if access.university:
                results.append(await _university_step(access, alfred))
    finally:
        _running = False
    return _report(results, time.monotonic() - started)


async def _run_step(alfred: Alfred, step: Step) -> tuple[Step, Optional[str], str]:
    try:
        reply = await asyncio.wait_for(alfred.handle_text(step.say), STEP_TIMEOUT)
    except asyncio.TimeoutError:
        return step, "нет ответа за минуту", ""
    except Exception as exc:
        log.exception("Самопроверка: ошибка на фразе «%s»", step.say)
        return step, f"ошибка внутри Альфреда ({type(exc).__name__})", ""
    text = "\n".join([reply.text] + [e.text for e in reply.extra])
    failed = next((f for f in FAILURES if f in text), None)
    if failed:
        return step, "Альфред ответил, что не смог", text
    try:
        return step, step.check(alfred, text), text
    except Exception as exc:
        log.exception("Самопроверка: не удалось проверить «%s»", step.say)
        return step, f"не удалось проверить ({type(exc).__name__})", text


UNIVERSITY = Step("вход на сайт расписания вуза", "Расписание вуза", lambda _a, _r: None)


async def _university_step(access: Access, alfred: Alfred) -> tuple[Step, Optional[str], str]:
    """🎓 Можно ли войти на сайт вуза и забрать пары на неделю (в распорядок ничего не записываем)."""
    try:
        today = alfred.today()
        items = await asyncio.wait_for(access.university.lessons(today, today + timedelta(days=7)), STEP_TIMEOUT)
    except asyncio.TimeoutError:
        return UNIVERSITY, "сайт вуза не ответил за минуту", ""
    except UniversityError as e:
        return UNIVERSITY, UNI_ERRORS.get(str(e), str(e)), ""
    except Exception as exc:
        log.exception("Самопроверка: расписание вуза")
        return UNIVERSITY, f"ошибка внутри Альфреда ({type(exc).__name__})", ""
    return UNIVERSITY, None, f"пар на неделю: {len(items)}"


def _report(results: list[tuple[Step, Optional[str], str]], seconds: float) -> Reply:
    ok = sum(1 for _s, problem, _t in results if problem is None)
    took = f"{int(seconds // 60)} мин {int(seconds % 60)} с" if seconds >= 60 else f"{int(seconds)} с"
    head = f"🎩 Самопроверка версии {VERSION}, Сэр!\n\n"
    if ok == len(results):
        passed = " · ".join(s.about for s, _p, _t in results)
        return Reply(f"{head}✅ {ok} из {len(results)} — всё работает! ({took})\n\n{passed}")
    lines = [f"⚠️ {ok} из {len(results)} — есть проблемы! ({took})", ""]
    for step, problem, text in results:
        if problem:
            answer = " ".join(text.split())[:160] or "—"
            lines.append(f"❌ {step.about}\n   фраза: «{step.say}»\n   что не так: {problem}\n   ответ: {answer}")
    lines += ["", "✅ Работает: " + " · ".join(s.about for s, p, _t in results if p is None)]
    return Reply(head + "\n".join(lines))


async def after_update(access: Access, send: Callable[[Reply], object], delay: float = 15) -> None:
    """После обновления (новая версия) — один раз прогнать самопроверку и прислать итог владельцу."""
    if access.users.setting("selftest_version") == VERSION:
        return                                             # эту версию уже проверяли (просто перезапуск)
    await asyncio.sleep(delay)                             # даём боту спокойно запуститься
    try:
        reply = await run_selftest(access)
    except Exception:
        log.exception("Самопроверка после обновления не удалась")
        reply = Reply(f"🎩 Сэр, самопроверку версии {VERSION} провести не удалось! Подробности — в логах Railway.")
    access.users.set_setting("selftest_version", VERSION)
    await send(reply)
