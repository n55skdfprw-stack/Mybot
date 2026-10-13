"""🎓 Расписание вуза: загрузка пар в распорядок, сверка с сайтом, сообщения об изменениях.
Сайт вуза заменён «подставным» (данные — в том же виде, что отдаёт timetable.lesgaft.spb.ru). Сегодня — чт 24.09.2026."""

import copy
from datetime import date

from alfred.core.selftest import run_selftest
from alfred.core.university import PREFIX, lesson_values, sync_and_report
from alfred.services.university import UniversityClient, UniversityError

from .test_access import make as make_access, run
from .test_selftest import IdealLLM
from .test_telegram_flow import Chatroom

GROUP = "АФК-М26-3"


PAIRS = {"09:45": 1, "11:30": 2, "13:30": 3, "15:15": 4}


def lesson(uid, day, start, end, discipline="Физическая реабилитация", kind="Лекция", place="Ауд. 421",
           groups=(GROUP,), subgroup=None):
    return {"id": f"{uid}:{day}", "date": day, "start": start, "end": end, "discipline": discipline, "kind": kind,
            "place": place, "groups": list(groups), "subgroup": subgroup,
            "teacher": "Иванов И.И., канд. пед. наук, доц.", "pairNo": PAIRS.get(start)}


class Site:
    """Подставной сайт вуза: отдаёт пары из списка; можно «сломать» пароль или связь."""

    def __init__(self):
        self.items = [
            lesson("a", "2026-09-25", "09:45", "11:15"),
            lesson("b", "2026-09-25", "11:30", "13:00", kind="Практическое занятие", place="Ауд. 83"),
            lesson("c", "2026-09-28", "13:30", "15:00", discipline="Анатомия", place="Ауд. 305"),
            lesson("d", "2026-10-01", "09:45", "11:15", discipline="Анатомия", place="Ауд. 305"),
            lesson("x", "2026-09-28", "09:45", "11:15", groups=("ДРУГАЯ-ГРУППА",)),
        ]
        self.error = None

    async def fetch(self, start, end):
        if self.error:
            raise UniversityError(self.error)
        return [copy.deepcopy(i) for i in self.items if start.isoformat() <= i["date"] <= end.isoformat()]

    def find(self, uid):
        return next(i for i in self.items if i["id"].startswith(uid + ":"))


def make(tmp_path):
    access, admin, llm = make_access(tmp_path)
    site = Site()
    access.university = UniversityClient("login", "secret", GROUP, fetch=site.fetch)
    alfred = access.alfred_for(access.owner)
    return access, alfred, site


def sync(access, alfred, manual=False):
    r = run(sync_and_report(alfred, access.university, access.users, manual=manual))
    return r.text if r else None


def uni_events(alfred):
    return [e for e in alfred.schedule.between(date(2026, 9, 1), date(2026, 12, 31))
            if e.external_id and e.external_id.startswith(PREFIX)]


# ---------------------------------------------------------------- как пара с сайта становится событием

def test_lesson_values():
    v = lesson_values(lesson("a", "2026-09-25", "09:45", "11:15"))
    assert (v["type"], v["title"], v["discipline"], v["location"]) == ("lecture", None, "Физическая реабилитация",
                                                                         "Ауд. 421")
    assert (v["date"], v["start_time"], v["end_time"]) == ("2026-09-25", "09:45", "11:15")
    assert v["comment"] == "Преподаватель: Иванов И.И." and v["external_id"].startswith("uni:")
    same = lesson_values({**lesson("a", "2026-09-25", "09:45", "11:15"), "id": "СОВСЕМ-ДРУГОЙ-НОМЕР"})
    assert same["external_id"] == v["external_id"]          # номер записи на сайте ключ не меняет
    assert lesson_values(lesson("b", "2026-09-25", "11:30", "13:00", kind="Практическое занятие"))["type"] == "practice"
    exam = lesson_values(lesson("e", "2026-12-20", "10:00", "12:00", kind="Экзамен", subgroup="2"))
    assert (exam["type"], exam["title"]) == ("other", "Экзамен") and "Подгруппа 2" in exam["comment"]
    assert lesson_values({"id": "z", "date": None, "start": "10:00"}) is None


# ---------------------------------------------------------------- первая загрузка и сверка

def test_first_sync_loads_lessons_with_reminders(tmp_path):
    access, alfred, site = make(tmp_path)
    text = sync(access, alfred)
    assert text.startswith(f"🎩 Готово, Сэр! Подключил расписание вуза — группа {GROUP}!")
    assert "Загрузил пар: 4 (до 1 октября)" in text
    evs = uni_events(alfred)
    assert len(evs) == 4                                      # пара чужой группы — не наша
    assert {e.type for e in evs} == {"lecture", "practice"}
    fri = alfred.schedule.day(date(2026, 9, 25))
    assert [e.start_time for e in fri] == ["09:45", "11:30"]
    notifs = alfred.schedule.notifs.pending_until(alfred.user_id, "2026-09-25T23:59")
    assert {n.event_id for n in notifs} == {e.id for e in fri}  # напоминания о парах — как об обычных лекциях


def test_no_changes_scheduled_is_silent_manual_says_up_to_date(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    assert sync(access, alfred) is None
    assert sync(access, alfred, manual=True).startswith("🎩 Сэр, расписание вуза актуально!\n\n📚 Пар впереди: 4")
    assert len(uni_events(alfred)) == 4                       # повторная сверка не плодит дубли


def test_room_and_time_changes_are_applied_and_reported(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    site.find("a")["place"] = "Ауд. 305"
    site.find("c")["start"], site.find("c")["end"] = "15:15", "16:45"
    text = sync(access, alfred)
    assert text.startswith("🎩 Сэр, в расписании вуза изменения!")
    assert "аудитория Ауд. 421 → Ауд. 305" in text
    assert "перенесли с пн 28 сен 13:30 на пн 28 сен 15:15" in text
    fri = alfred.schedule.day(date(2026, 9, 25))
    assert fri[0].location == "Ауд. 305"
    assert [e.start_time for e in alfred.schedule.day(date(2026, 9, 28))] == ["15:15"]


def test_cancelled_and_moved_lessons(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    site.items.remove(site.find("b"))                         # практику отменили
    site.items.remove(site.find("d"))                         # анатомию 1.10 перенесли на 2.10
    site.items.append(lesson("d", "2026-10-02", "11:30", "13:00", discipline="Анатомия", place="Ауд. 305"))
    text = sync(access, alfred)
    assert "❌ Отменена: Практика «Физическая реабилитация», пт 25 сен 11:30" in text
    assert "🔄 Лекция «Анатомия»: перенесли с чт 1 окт 09:45 на пт 2 окт 11:30, Ауд. 305" in text
    assert [e.start_time for e in alfred.schedule.day(date(2026, 9, 25))] == ["09:45"]
    assert alfred.schedule.day(date(2026, 10, 1)) == [] and len(alfred.schedule.day(date(2026, 10, 2))) == 1


def test_new_lesson_soon_is_reported_far_one_is_silent(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    site.items.append(lesson("n", "2026-09-29", "15:15", "16:45", discipline="Биомеханика"))
    site.items.append(lesson("f", "2026-10-12", "09:45", "11:15", discipline="Биомеханика"))
    text = sync(access, alfred)
    assert "➕ Новая пара: Лекция «Биомеханика», вт 29 сен 15:15, Ауд. 421" in text
    assert "12 окт" not in text                               # через 2+ недели — добавлена тихо
    assert len(alfred.schedule.day(date(2026, 10, 12))) == 1


# ---------------------------------------------------------------- как выглядит в распорядке

def test_pairs_have_numbers_and_blank_line_between(tmp_path):
    access, alfred, site = make(tmp_path)
    alfred.schedule.create({"type": "training", "date": "2026-09-25", "start_time": "18:00"}, alfred.now())
    alfred.schedule.create({"type": "training", "date": "2026-09-25", "start_time": "19:00"}, alfred.now())
    sync(access, alfred)
    day = alfred.schedule_day_view(date(2026, 9, 25)).text
    assert ("🎩 Ваш распорядок на завтра, Сэр!\n\n"
            "1-я пара · 09:45–11:15 — Лекция\n📚 Физическая реабилитация\n🚪 Ауд. 421\n"
            "💬 Преподаватель: Иванов И.И.\n\n"
            "2-я пара · 11:30–13:00 — Практика\n") in day
    assert "\n\n18:00 — Тренировка\n19:00 — Тренировка" in day      # свои короткие события — компактно


def test_pair_numbers_added_quietly_to_already_loaded_lessons(tmp_path):
    """Пары загружены прежней версией (без номеров) — номера дописываются молча, без «изменений»."""
    import json
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    key = f"uni_known:{alfred.user_id}"
    old = {k: {f: v for f, v in val.items() if f != "pair_no"} for k, val in json.loads(access.users.setting(key)).items()}
    access.users.set_setting(key, json.dumps(old, ensure_ascii=False))
    for e in uni_events(alfred):
        alfred.schedule.update(e, {"pair_no": None}, alfred.now())
    assert sync(access, alfred) is None
    assert [e.pair_no for e in alfred.schedule.day(date(2026, 9, 25))] == [1, 2]


# ---------------------------------------------------------------- живой спам 13.10: сайт меняет номера записей

def test_site_renumbering_lessons_is_not_a_change(tmp_path):
    """Сайт при каждом обновлении выдаёт парам новые номера — это НЕ перенос и НЕ «расписание на месте»."""
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    for n in range(3):
        for i in site.items:
            i["id"] = f"новый-номер-{n}-{i['id']}"
        assert sync(access, alfred) is None                   # ни «перенесли с 09:45 на 09:45», ни «на месте»
    assert len(uni_events(alfred)) == 4
    assert "актуально" in sync(access, alfred, manual=True)


def test_lessons_saved_by_old_version_are_rekeyed_quietly(tmp_path):
    """Пары, загруженные 8.31–8.36 (ключ — номер записи сайта), перепривязываются молча, комментарий сохраняется."""
    import json
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    key = f"uni_known:{alfred.user_id}"
    old_known = {}
    for e in uni_events(alfred):
        old_ext = f"uni:{e.id}-старый:{e.date.isoformat()}"
        alfred.schedule.update(e, {"external_id": old_ext}, alfred.now())
        old_known[old_ext] = {k: v for k, v in e.as_values().items()
                              if k in ("type", "title", "date", "start_time", "end_time", "location", "discipline")}
    lec = alfred.schedule.day(date(2026, 9, 25))[0]
    alfred.schedule.update(lec, {"comment": "взять конспект"}, alfred.now())
    access.users.set_setting(key, json.dumps(old_known, ensure_ascii=False))
    assert sync(access, alfred) is None
    evs = uni_events(alfred)
    assert len(evs) == 4 and not any(":" in e.external_id[4:] for e in evs)
    assert alfred.schedule.get(lec.id).comment == "взять конспект"            # та же запись, не пересоздана
    assert sync(access, alfred) is None


def test_real_move_and_room_change_are_reported_exactly(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    site.find("a")["start"], site.find("a")["end"] = "13:30", "15:00"       # пару перенесли на другое время
    site.find("c")["place"] = "Ауд. 410"                                     # сменилась аудитория
    text = sync(access, alfred)
    assert "🔄 Лекция «Физическая реабилитация»: перенесли с пт 25 сен 09:45 на пт 25 сен 13:30" in text
    assert "✏️ Лекция «Анатомия», пн 28 сен 13:30: аудитория Ауд. 305 → Ауд. 410" in text
    assert text.count("\n") == 3                                             # заголовок, пустая строка, 2 строки


# ---------------------------------------------------------------- записи человека

def test_own_events_and_own_edits_are_kept(tmp_path):
    access, alfred, site = make(tmp_path)
    own = alfred.schedule.create({"type": "training", "date": "2026-09-25", "start_time": "18:00"}, alfred.now())
    sync(access, alfred)
    lec = alfred.schedule.day(date(2026, 9, 25))[0]
    alfred.schedule.update(lec, {"comment": "взять конспект"}, alfred.now())   # человек дописал своё
    assert sync(access, alfred) is None
    assert alfred.schedule.get(lec.id).comment == "взять конспект"            # сайт не менялся — не трогаем
    assert alfred.schedule.get(own.id) is not None                            # тренировка на месте


def test_lesson_deleted_by_user_is_not_brought_back(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    lec = alfred.schedule.day(date(2026, 9, 25))[0]
    alfred.schedule.delete([lec], alfred.now())               # «лекции не будет»
    assert sync(access, alfred) is None
    assert [e.start_time for e in alfred.schedule.day(date(2026, 9, 25))] == ["11:30"]


def test_lesson_entered_by_hand_is_adopted_not_duplicated(tmp_path):
    access, alfred, site = make(tmp_path)
    alfred.schedule.create({"type": "lecture", "date": "2026-09-25", "start_time": "09:45"}, alfred.now())
    sync(access, alfred)
    fri = alfred.schedule.day(date(2026, 9, 25))
    assert [e.start_time for e in fri] == ["09:45", "11:30"]
    assert fri[0].external_id.startswith("uni:") and fri[0].location == "Ауд. 421"


def test_after_clean_slate_lessons_come_back(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    alfred.schedule.events.db.wipe_user_data(alfred.user_id)  # «🧹 Чистый лист»
    text = sync(access, alfred)
    assert text.startswith("🎩 Сэр, расписание вуза на месте!\n\n📚 Загрузил пары заново: 4 (до 1 октября)")
    assert len(uni_events(alfred)) == 4
    assert sync(access, alfred) is None                       # дальше — как обычно, без повторов


def test_undo_after_clean_slate_does_not_leave_double_lessons(tmp_path):
    """«Чистый лист» → пары подгрузились заново → «Отмени последнее» вернул и старые. Лишние убираются."""
    from .test_access import run as arun
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    alfred.handle_callback("confirm:reset_all")
    sync(access, alfred)
    arun(alfred.handle_text("Отмени последнее"))
    assert len(uni_events(alfred)) == 8                       # вернулись старые + свежие
    sync(access, alfred)
    assert len(uni_events(alfred)) == 4
    assert [e.start_time for e in alfred.schedule.day(date(2026, 9, 25))] == ["09:45", "11:30"]


# ---------------------------------------------------------------- сбои

def test_wrong_password_reported_once(tmp_path):
    access, alfred, site = make(tmp_path)
    site.error = "wrong_password"
    first = sync(access, alfred)
    assert "логин или пароль не подходят" in first and "UNI_LOGIN" in first
    assert sync(access, alfred) is None                       # та же ошибка — не напоминаем каждые 30 минут
    assert "не подходят" in sync(access, alfred, manual=True) # по просьбе — говорим всегда
    assert "secret" not in first                              # пароль в сообщения не попадает


def test_site_down_is_silent_and_keeps_lessons(tmp_path):
    access, alfred, site = make(tmp_path)
    sync(access, alfred)
    site.error = "unavailable"
    assert sync(access, alfred) is None
    assert len(uni_events(alfred)) == 4
    assert "не отвечает" in sync(access, alfred, manual=True)


def test_empty_answer_does_not_wipe_lessons(tmp_path):
    access, alfred, site = make(tmp_path)
    site.items += [lesson(f"m{i}", "2026-09-30", f"1{i}:00", f"1{i}:45") for i in range(3)]
    sync(access, alfred)
    site.items = []                                           # сайт сбойнул и отдал пустоту
    sync(access, alfred)
    assert len(uni_events(alfred)) == 7


# ---------------------------------------------------------------- Telegram и самопроверка

def test_owner_can_ask_to_update(tmp_path):
    room = Chatroom(tmp_path)
    room.say("Обнови расписание вуза")
    assert "ещё не подключено" in room.tg.sent()[-1] and "UNI_GROUP" in room.tg.sent()[-1]
    site = Site()
    room.access.university = UniversityClient("login", "secret", GROUP, fetch=site.fetch)
    room.say("Обнови расписание вуза")
    assert "Подключил расписание вуза" in room.tg.sent()[-1]
    room.say("расписание вуза")
    assert "актуально" in room.tg.sent()[-1]


def test_clean_slate_reloads_lessons_right_away(tmp_path):
    """Нажали «🧹 Да, начать с чистого листа» — Альфред всё стёр и сразу подгрузил пары вуза."""
    import asyncio
    room = Chatroom(tmp_path)
    room.access.university = UniversityClient("login", "secret", GROUP, fetch=Site().fetch)
    room.say("Обнови расписание вуза")
    room.alfred.tasks.create("Купить хлеб", None)

    async def press_and_wait():
        await room.feed(room.press("confirm:reset_all"))
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending)                           # подгрузка пар идёт в фоне — дожидаемся

    run(press_and_wait())
    assert any("Всё очищено" in t for t in room.tg.edited())
    assert room.tg.sent()[-1].startswith("🎩 Сэр, расписание вуза на месте!")
    assert room.alfred.tasks.active() == []                   # всё остальное стёрто
    assert len(uni_events(room.alfred)) == 4                  # а пары — снова в распорядке


def test_clean_slate_without_university_works_as_before(tmp_path):
    room = Chatroom(tmp_path)
    room.alfred.tasks.create("Купить хлеб", None)
    run(room.feed(room.press("confirm:reset_all")))
    assert any("Всё очищено" in t for t in room.tg.edited()) and room.alfred.tasks.active() == []
    assert not any("расписание вуза" in t for t in room.tg.sent())


def test_selftest_checks_university_login(tmp_path):
    from alfred.brain.brain import Brain
    access, alfred, site = make(tmp_path)
    access.brain = Brain(IdealLLM())
    site.error = "wrong_password"
    r = run(run_selftest(access))
    assert "❌ Расписание вуза" in r.text and "логин или пароль не подходят" in r.text
    assert uni_events(alfred) == []                           # самопроверка в распорядок ничего не пишет
