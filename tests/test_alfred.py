"""Тесты первого этапа. ИИ заменён «подставным», который возвращает заранее заданный JSON."""

import asyncio
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from alfred.brain.brain import Brain
from alfred.brain.llm_client import LLMError
from alfred.brain.parser import ParseError, parse
from alfred.core.alfred import Alfred
from alfred.database.db import Database
from alfred.database.repositories import ContextRepository, NoteRepository, TaskRepository, UserRepository
from alfred.database.schedule_repo import EventRepository, NotificationRepository, RuleRepository
from alfred.services.schedule import ScheduleService
from alfred.services.notes import NoteService
from alfred.services.tasks import TaskService

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 9, 23, 11, 0, tzinfo=TZ)  # среда
TODAY = NOW.date()


class FakeLLM:
    """Отдаёт по очереди заранее заданные ответы."""

    def __init__(self):
        self.queue: list = []

    def said(self, **fields):
        self.queue.append(json.dumps(fields, ensure_ascii=False))

    async def complete(self, system, user, pro=False):
        if not self.queue:
            raise AssertionError("LLM called unexpectedly")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(tmp_path, llm=None):
    db = Database(str(tmp_path / "test.db"))
    db.migrate()
    uid = UserRepository(db).ensure(1, "Europe/Moscow", "Санкт-Петербург")
    llm = llm or FakeLLM()
    schedule = ScheduleService(EventRepository(db), RuleRepository(db), NotificationRepository(db), uid)
    alfred = Alfred(Brain(llm), TaskService(TaskRepository(db), uid), NoteService(NoteRepository(db), uid),
                    schedule, ContextRepository(db), uid, TZ, clock=lambda: NOW)
    return alfred, llm, db


def run(coro):
    return asyncio.run(coro)


def style_ok(text: str):
    """Каждая реплика Альфреда начинается с 🎩, а первая строка заканчивается на ! или ?."""
    assert text.startswith("🎩"), text
    first = text.split("\n")[0]
    assert first.endswith(("!", "?")), first


# ---------------------------------------------------------------- дела

def test_create_task(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить корм коту", due_when="завтра")
    r = run(a.handle_text("Купить корм коту завтра"))
    style_ok(r.text)
    assert "Купить корм коту — завтра" in r.text
    assert a.tasks.active()[0].due_date == date(2026, 9, 24)


def test_duplicate_blocked_and_forced(tmp_path):
    a, llm, _ = make(tmp_path)
    for _ in range(2):
        llm.said(intent="CREATE_TASK", title="Позвонить маме")
    run(a.handle_text("Позвонить маме"))
    r = run(a.handle_text("Позвонить маме"))
    assert "уже есть" in r.text
    llm.said(intent="CREATE_TASK", title="Позвонить маме", force_duplicate=True)
    run(a.handle_text("Добавь ещё одно: позвонить маме"))
    assert len(a.tasks.active()) == 2


def test_reschedule_task_keeps_title(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить корм коту", due_when="завтра")
    run(a.handle_text("Купить корм коту завтра"))
    llm.said(intent="UPDATE_TASK", target="корм", new_due_when="на пятницу")
    r = run(a.handle_text("Перенеси корм на пятницу"))
    style_ok(r.text)
    t = a.tasks.active()[0]
    assert t.title == "Купить корм коту" and t.due_date == date(2026, 9, 25)
    assert "послезавтра" in r.text


def test_correction_uses_last_object(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Подготовить отчёт", due_when="в пятницу")
    run(a.handle_text("Подготовить отчёт в пятницу"))
    llm.said(intent="UPDATE_TASK", target="LAST", new_due_when="на субботу")
    run(a.handle_text("Нет, на субботу"))
    assert a.tasks.active()[0].due_date == date(2026, 9, 26)
    assert len(a.tasks.active()) == 1  # исправление, а не новая запись


def test_complete_and_delete_task(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить корм коту")
    llm.said(intent="CREATE_TASK", title="Подготовить отчёт")
    run(a.handle_text("x")); run(a.handle_text("y"))
    llm.said(intent="COMPLETE_TASK", target="корм")
    r = run(a.handle_text("Отметь корм выполненным"))
    style_ok(r.text)
    llm.said(intent="DELETE_TASK", target="отчёт")
    r = run(a.handle_text("Удали задачу про отчёт"))
    style_ok(r.text)
    assert a.tasks.active() == []


def test_not_found(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="COMPLETE_TASK", target="самолёт")
    r = run(a.handle_text("Отметь самолёт"))
    assert "не нашёл" in r.text and r.text.endswith("?")


def test_ambiguity_then_pick(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Позвонить Сергею")
    llm.said(intent="CREATE_TASK", title="Написать Сергею")
    run(a.handle_text("x")); run(a.handle_text("y"))
    llm.said(intent="DELETE_TASK", target="Сергею")
    r = run(a.handle_text("Удали дело про Сергея"))
    assert r.text.endswith("?") and r.buttons
    task_id = a.tasks.find("Написать Сергею")[0].id
    r2 = a.handle_callback(f"pick:{task_id}")
    assert r2.edit and "Написать Сергею" in r2.text
    assert [t.title for t in a.tasks.active()] == ["Позвонить Сергею"]


def test_mass_delete_requires_confirmation(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Раз")
    run(a.handle_text("x"))
    llm.said(intent="DELETE_TASK", scope="all")
    r = run(a.handle_text("Удали все дела"))
    assert r.text.endswith("?") and len(a.tasks.active()) == 1
    a.handle_callback("confirm:no")
    assert len(a.tasks.active()) == 1
    a.handle_callback("confirm:del_all_tasks")
    assert a.tasks.active() == []


def test_task_button_completes(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Раз")
    run(a.handle_text("x"))
    tid = a.tasks.active()[0].id
    r = a.handle_callback(f"task:done:{tid}")
    assert r.edit and a.tasks.active() == []


# ---------------------------------------------------------------- заметки

def test_note_create_search_update_delete(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="Паспорт лежит в верхнем ящике")
    r = run(a.handle_text("Запиши, что паспорт лежит в верхнем ящике"))
    style_ok(r.text)
    llm.said(intent="SEARCH_NOTE", query="паспорт")
    r = run(a.handle_text("Что я записывал про паспорт?"))
    assert "верхнем ящике" in r.text
    llm.said(intent="UPDATE_NOTE", target="паспорт", replace_from="верхнем", replace_to="нижнем")
    r = run(a.handle_text("В заметке про паспорт поменяй верхний ящик на нижний"))
    assert "нижнем ящике" in r.text
    llm.said(intent="DELETE_NOTE", target="паспорт")
    run(a.handle_text("Удали заметку про паспорт"))
    assert a.notes.all() == []


def test_negation_removes_fragment(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="К врачу взять полис и паспорт")
    run(a.handle_text("x"))
    llm.said(intent="UPDATE_NOTE", target="врач", replace_from=" и паспорт", replace_to="")
    run(a.handle_text("К врачу паспорт уже не нужен"))
    assert a.notes.all()[0].content == "К врачу взять полис"


# ---------------------------------------------------------------- контекст

def test_missing_parameter_and_answer(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE")
    r = run(a.handle_text("Сделай заметку"))
    assert r.text.endswith("?")
    llm.said(intent="ANSWER", answer="Код от домофона 1234")
    run(a.handle_text("Код от домофона 1234"))
    assert a.notes.all()[0].content == "Код от домофона 1234"


def test_cancel_resets_context(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK")
    run(a.handle_text("Добавь дело"))
    r = run(a.handle_text("Отмена"))
    style_ok(r.text)
    llm.said(intent="ANSWER", answer="что-то")
    r = run(a.handle_text("что-то"))
    assert a.tasks.active() == []  # ответ после отмены не создаёт дело


def test_topic_change_drops_question(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE")
    run(a.handle_text("Сделай заметку"))
    llm.said(intent="SHOW_TASKS")
    r = run(a.handle_text("Покажи мои дела"))
    assert a.notes.all() == [] and "дел" in r.text


# ---------------------------------------------------------------- проверки дел

def test_checks(tmp_path):
    a, llm, _ = make(tmp_path)
    assert a.check_message("morning") is None  # нет дел — не беспокоим
    llm.said(intent="CREATE_TASK", title="Купить корм коту")
    llm.said(intent="CREATE_TASK", title="Будущее дело", due_when="10 октября")
    run(a.handle_text("Купить корм коту")); run(a.handle_text("Будущее дело 10 октября"))
    m = a.check_message("morning")
    assert "Купить корм коту" in m.text and "Будущее дело" not in m.text
    style_ok(m.text)
    d = a.check_message("day")
    assert d.buttons and len(d.buttons) == 3
    r = a.handle_callback("chk:all:evening")
    assert "Хорошего вечера" in r.text and r.clear_source_buttons
    assert [t.title for t in a.tasks.active()] == ["Будущее дело"]


# ---------------------------------------------------------------- ошибки и надёжность

def test_ai_unavailable(tmp_path):
    llm = FakeLLM()
    llm.queue = [LLMError("down"), LLMError("down")]
    a, _, _ = make(tmp_path, llm)
    r = run(a.handle_text("Купить хлеб"))
    assert "недоступен" in r.text and a.tasks.active() == []


def test_bad_json_falls_back_to_pro_then_unknown(tmp_path):
    llm = FakeLLM()
    llm.queue = ["это не json", "и это не json"]
    a, _, _ = make(tmp_path, llm)
    r = run(a.handle_text("???"))
    assert r.text.endswith("?")


def test_db_error_no_false_success(tmp_path, monkeypatch):
    a, llm, _ = make(tmp_path)
    import sqlite3

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk")
    monkeypatch.setattr(a.tasks.repo, "add", boom)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    r = run(a.handle_text("Купить хлеб"))
    assert "не удалось" in r.text and "Записал" not in r.text


def test_data_survives_restart(tmp_path):
    a, llm, db = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить хлеб"))
    a2, _, _ = make(tmp_path)  # «перезапуск» с той же базой
    assert [t.title for t in a2.tasks.active()] == ["Купить хлеб"]


def test_parser_strips_code_fence():
    r = parse('```json\n{"intent":"CREATE_TASK","title":"Хлеб","due_date":"2026-09-24"}\n```')
    assert r.intent == "CREATE_TASK" and r.due_date == date(2026, 9, 24)
    with pytest.raises(ParseError):
        parse('{"intent":"HACK"}')


def test_menu_buttons_do_not_call_ai(tmp_path):
    a, llm, _ = make(tmp_path)
    r = run(a.handle_text("📋 Ваши дела"))
    style_ok(r.text)
    r = run(a.handle_text("💰 Ваши финансы"))
    style_ok(r.text)  # раздел в разработке, но отвечает вежливо
    r = run(a.handle_text("🕰️ Ваш распорядок"))
    style_ok(r.text)


def test_all_replies_follow_style(tmp_path):
    """Грубая проверка: в текстах ядра нет предложений, которые заканчиваются точкой."""
    src = open("alfred/core/alfred.py", encoding="utf-8").read()
    phrases = re.findall(r'"(🎩[^"]*)"', src)
    for p in phrases:
        assert not p.rstrip().endswith("."), p


# ---------------------------------------------------------------- даты (среда, 23 сентября 2026)

@pytest.mark.parametrize("phrase,expected", [
    ("сегодня", date(2026, 9, 23)),
    ("завтра", date(2026, 9, 24)),
    ("послезавтра", date(2026, 9, 25)),
    ("в пятницу", date(2026, 9, 25)),
    ("на пятницу", date(2026, 9, 25)),
    ("Нет давай на пятницу", date(2026, 9, 25)),
    ("в среду", date(2026, 9, 30)),
    ("в следующую пятницу", date(2026, 10, 2)),
    ("в понедельник", date(2026, 9, 28)),
    ("на выходных", date(2026, 9, 26)),
    ("через 3 дня", date(2026, 9, 26)),
    ("через два дня", date(2026, 9, 25)),
    ("через неделю", date(2026, 9, 30)),
    ("через 2 недели", date(2026, 10, 7)),
    ("через месяц", date(2026, 10, 23)),
    ("15 октября", date(2026, 10, 15)),
    ("5 марта", date(2027, 3, 5)),
    ("1 мая", date(2027, 5, 1)),
    ("3 марта", date(2027, 3, 3)),
    ("01.10", date(2026, 10, 1)),
    ("01.10.2027", date(2027, 10, 1)),
])
def test_dates(phrase, expected):
    from alfred.brain.dates import parse_date
    assert parse_date(phrase, TODAY) == expected


def test_live_scenario_from_telegram(tmp_path):
    """Сценарий, на котором нашлась ошибка: завтра → на пятницу."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить корм собаке", due_when="завтра")
    r = run(a.handle_text("Купить корм собаке завтра"))
    assert "— завтра" in r.text
    llm.said(intent="UPDATE_TASK", target="LAST", new_due_when="на пятницу")
    run(a.handle_text("Нет давай на пятницу"))
    assert a.tasks.active()[0].due_date == date(2026, 9, 25)


def test_ai_invented_date_is_ignored(tmp_path):
    """Если ИИ сам «посчитал» дату, а в словах даты нет — дата не ставится."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Позвонить маме", due_date="2026-09-29")
    run(a.handle_text("Позвонить маме"))
    assert a.tasks.active()[0].due_date is None


def test_date_fallback_from_message(tmp_path):
    """ИИ забыл процитировать дату — Альфред найдёт её в сообщении сам."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить хлеб в субботу"))
    assert a.tasks.active()[0].due_date == date(2026, 9, 26)


def test_date_words_removed_from_title(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб завтра", due_when="завтра")
    run(a.handle_text("Купить хлеб завтра"))
    t = a.tasks.active()[0]
    assert t.title == "Купить хлеб" and t.due_date == date(2026, 9, 24)


def test_titles_capitalized(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="купить молоко")
    r = run(a.handle_text("купить молоко"))
    assert a.tasks.active()[0].title == "Купить молоко" and "⭕ Купить молоко" in r.text


def test_tasks_view_has_no_hint_phrase(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Раз")
    run(a.handle_text("x"))
    assert "Нажмите на дело" not in a.tasks_view().text


@pytest.mark.parametrize("ai_target", [None, "LAST"])
def test_done_uses_words_not_last_object(tmp_path, ai_target):
    """Живая ошибка: после переименования отчёта «Сделал хлеб» вычеркнуло отчёт."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    llm.said(intent="CREATE_TASK", title="Подготовить отчет")
    run(a.handle_text("x")); run(a.handle_text("y"))
    llm.said(intent="UPDATE_TASK", target="подготовить отчет", new_title="Подготовьте отчет")
    run(a.handle_text("Переименуй подготовить отчет в подготовьте отчет"))
    llm.said(intent="COMPLETE_TASK", target=ai_target)
    r = run(a.handle_text("Сделал хлеб"))
    assert "хлеб" in r.text
    assert [t.title for t in a.tasks.active()] == ["Подготовьте отчет"]


def test_pronoun_still_uses_last_object(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    llm.said(intent="CREATE_TASK", title="Подготовить отчет")
    run(a.handle_text("x")); run(a.handle_text("y"))
    llm.said(intent="COMPLETE_TASK", target="LAST")
    run(a.handle_text("Сделал его"))
    assert [t.title for t in a.tasks.active()] == ["Купить хлеб"]


def test_wrong_ai_target_falls_back_to_message(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    llm.said(intent="CREATE_TASK", title="Позвонить маме")
    run(a.handle_text("x")); run(a.handle_text("y"))
    llm.said(intent="COMPLETE_TASK", target="самолёт")
    run(a.handle_text("Позвонил маме, отметь"))
    assert [t.title for t in a.tasks.active()] == ["Купить хлеб"]


def test_exact_milk_scenario_from_logs(tmp_path):
    """Точная копия живой ошибки: переименование без названия задело молоко."""
    a, llm, _ = make(tmp_path)
    for title in ("Купить корм собаке", "Купить хлеб", "Подготовить отчет", "Купить молоко"):
        llm.said(intent="CREATE_TASK", title=title)
        run(a.handle_text(title))
    llm.said(intent="UPDATE_TASK", target=None, new_title="Подготовьте отчет")
    run(a.handle_text("Переименуй подготовить отчет в подготовьте отчет"))
    llm.said(intent="COMPLETE_TASK", target=None)
    run(a.handle_text("Сделал хлеб"))
    titles = sorted(t.title for t in a.tasks.active())
    assert titles == ["Купить корм собаке", "Купить молоко", "Подготовьте отчет"]


def test_restore_task(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить молоко", due_when="в пятницу")
    llm.said(intent="CREATE_TASK", title="Купить хлеб")
    run(a.handle_text("Купить молоко в пятницу")); run(a.handle_text("Купить хлеб"))
    llm.said(intent="COMPLETE_TASK", target="молоко")
    run(a.handle_text("Сделал молоко"))
    llm.said(intent="RESTORE_TASK", target="молоко")
    r = run(a.handle_text("Верни молоко, я его не купил"))
    style_ok(r.text)
    milk = [t for t in a.tasks.active() if t.title == "Купить молоко"][0]
    assert milk.due_date == date(2026, 9, 25)  # дата сохранилась


def test_restore_when_already_in_list(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить молоко")
    run(a.handle_text("x"))
    llm.said(intent="COMPLETE_TASK", target="молоко")
    run(a.handle_text("Сделал молоко"))
    llm.said(intent="CREATE_TASK", title="Купить молоко")
    run(a.handle_text("Купить молоко"))
    llm.said(intent="RESTORE_TASK", target="молоко")
    r = run(a.handle_text("Верни молоко"))
    assert "уже есть" in r.text and len(a.tasks.active()) == 1


@pytest.mark.parametrize("phrase", ["Верни молоко", "Верни молоко я его не купил",
                                    "Я не купил молоко", "Зря вычеркнул молоко"])
def test_restore_guard_when_ai_says_create(tmp_path, phrase):
    """Живая ошибка: ИИ понял «Верни молоко» как новое дело «Вернуть молоко»."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Купить молоко")
    run(a.handle_text("x"))
    llm.said(intent="COMPLETE_TASK", target="молоко")
    run(a.handle_text("Сделал молоко"))
    llm.said(intent="CREATE_TASK", title="Вернуть молоко")
    r = run(a.handle_text(phrase))
    assert "Вернул" in r.text
    assert [t.title for t in a.tasks.active()] == ["Купить молоко"]


def test_guard_does_not_break_normal_create(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Вернуть книгу в библиотеку")
    run(a.handle_text("Вернуть книгу в библиотеку"))
    assert [t.title for t in a.tasks.active()] == ["Вернуть книгу в библиотеку"]


@pytest.mark.parametrize("frm,to", [("верхний ящик", "нижний ящик"), ("верхний", "нижний"),
                                    ("верхнем ящике", "нижнем ящике")])
def test_note_replace_with_other_endings(tmp_path, frm, to):
    """Живая ошибка: в заметке «верхнем ящике», а ИИ прислал «верхний ящик»."""
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="Паспорт в верхнем ящике")
    run(a.handle_text("Паспорт в верхнем ящике"))
    llm.said(intent="UPDATE_NOTE", target="паспорт", replace_from=frm, replace_to=to)
    r = run(a.handle_text("В заметке про паспорт поменяй верхний ящик на нижний"))
    assert a.notes.all()[0].content == "Паспорт в нижнем ящике"
    assert "нижнем ящике" in r.text


def test_fuzzy_replace_new_noun():
    from alfred.services.notes import fuzzy_replace
    assert fuzzy_replace("Ключи в ящике", "ящик", "шкаф") == "Ключи в шкафе"
    assert fuzzy_replace("Ключи в ящике", "самолёт", "шкаф") is None


def test_notes_view_has_no_hint_phrase(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_NOTE", content="Код домофона 1234")
    run(a.handle_text("x"))
    assert "Чтобы найти" not in a.notes_view().text


# ---------------------------------------------------------------- версия 3.5

def test_task_without_date_stays_without_date(tmp_path):
    a, llm, _ = make(tmp_path)
    # ИИ по ошибке добавил «сегодня», хотя пользователь дату не называл
    llm.said(intent="CREATE_TASK", title="Выкинуть мусор", due_when="сегодня")
    r = run(a.handle_text("Выкинуть мусор"))
    assert a.tasks.active()[0].due_date is None
    assert "сегодня" not in r.text


def test_task_with_date_keeps_date(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Заправить кровать", due_when="сегодня")
    r = run(a.handle_text("Сегодня заправить кровать"))
    assert a.tasks.active()[0].due_date == date(2026, 9, 23) and "сегодня" in r.text


# ---------------------------------------------------------------- версия 3.6: дела кнопками

def test_tasks_compact_toggle_and_list(tmp_path):
    a, llm, _ = make(tmp_path)
    llm.said(intent="CREATE_TASK", title="Выкинуть мусор")
    llm.said(intent="CREATE_TASK", title="Позвонить папе", due_when="завтра")
    run(a.handle_text("Выкинуть мусор")); run(a.handle_text("Позвонить папе завтра"))
    v = a.tasks_view()
    labels = [row[0][0] for row in v.buttons]
    assert "⭕ Выкинуть мусор" in labels and "⭕ Позвонить папе · завтра" in labels
    assert labels[-1] == "📋 Посмотреть список"
    trash = next(row[0][1] for row in v.buttons if row[0][0] == "⭕ Выкинуть мусор")
    v = a.handle_callback(trash)                       # отметили мусор
    labels = [row[0][0] for row in v.buttons]
    assert "🟢 Выкинуть мусор" in labels and "⭕ Выкинуть мусор" not in labels
    lst = a.handle_callback("task:list")
    assert "⭕ Не сделано:\nПозвонить папе — завтра" in lst.text
    assert "🟢 Сделано сегодня:\nВыкинуть мусор" in lst.text
    v = a.handle_callback("task:open")
    undo = next(row[0][1] for row in v.buttons if row[0][0] == "🟢 Выкинуть мусор")
    v = a.handle_callback(undo)                        # вернули
    assert "⭕ Выкинуть мусор" in [row[0][0] for row in v.buttons]
