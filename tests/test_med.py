"""Тесты седьмого этапа — «Медкарта». Сегодня — четверг, 24 сентября 2026, 12:00."""

from datetime import date, datetime, timedelta

from .test_birthdays import make, say, run, at
from .test_alfred import TZ, style_ok

ANGINA = {"illness": "Ангина", "doctor": "терапевт", "notes": "полоскать горло",
          "drugs": [{"name": "Амоксициллин", "dose": "500 мг", "per_day": 3, "meal": "после еды", "days": 7}]}
TEXT = "Заболел ангиной, терапевт назначил амоксициллин 500 мг 3 раза в день после еды 7 дней и полоскать горло"


def clock(a, day: date, hh: int, mm: int = 0):
    a._clock = lambda: datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)


def test_create_case(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    style_ok(r.text)
    assert r.text == (
        "🎩 Записал в медкарту, Сэр! Поправляйтесь!\n\n🤒 Ангина\n"
        "📅 С 24 сентября · болею (день 1)\n👨‍⚕️ Врач: Терапевт\n\n"
        "Лечение:\n💊 Амоксициллин\n   500 мг · 3 раза в день · после еды · 7 дней\n"
        "   ⏰ 08:00, 14:00, 20:00 · до 30 сен включительно\n\n📝 Полоскать горло\n\n"
        "⏰ Буду напоминать о приёме, Сэр.")


def test_ai_missed_details_taken_from_words(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, "Насморк, називин по 2 капли утром и вечером неделю", intent="MED_CASE",
        med={"illness": "Насморк", "drugs": [{"name": "називин"}]})
    d = a.med.cases()[0].drugs[0]
    assert (d.dose, d.per_day, d.times, d.days) == ("2 капли", 2, ["09:00", "21:00"], 7)


def test_reminders_took_and_snooze(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    clock(a, date(2026, 9, 24), 13, 59)
    assert a.collect_med_reminders() == []
    clock(a, date(2026, 9, 24), 14, 0)
    [r] = a.collect_med_reminders()
    style_ok(r.text)
    assert r.text == ("🎩 Сэр, пора принять лекарство!\n\n💊 Амоксициллин — 500 мг · после еды\n"
                      "🤒 Ангина\n📅 День 1 из 7")
    assert a.collect_med_reminders() == []                 # второй раз тот же приём не шлём
    r2 = a.handle_callback(r.buttons[0][1][1])             # ⏰ Через 15 минут
    assert r2.text == "🎩 Хорошо, Сэр! Напомню про Амоксициллин в 14:15."
    clock(a, date(2026, 9, 24), 14, 15)
    [again] = a.collect_med_reminders()
    r3 = a.handle_callback(again.buttons[0][0][1])         # ✅ Принял
    assert r3.text == "🎩 Отлично, Сэр!\n\n✅ Амоксициллин — принято в 14:15"


def test_reminders_stop_after_course_and_when_late(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    clock(a, date(2026, 9, 30), 20, 0)
    assert len(a.collect_med_reminders()) == 1             # последний день курса
    clock(a, date(2026, 10, 1), 8, 0)
    assert a.collect_med_reminders() == []                 # курс закончился
    clock(a, date(2026, 9, 25), 8, 20)
    assert a.collect_med_reminders() == []                 # бот проспал больше 15 минут — не спамим


def test_recover_stops_reminders_and_history(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    clock(a, date(2026, 9, 28), 12, 0)
    r = say(a, llm, "Я выздоровел", intent="UNKNOWN")      # ИИ не понял — Альфред понял
    assert r.text == ("🎩 Рад слышать, Сэр! Выздоровление записано!\n\n✅ Ангина · 24 сен – 28 сен\n"
                      "Напоминания о лекарствах остановлены.")
    clock(a, date(2026, 9, 29), 8, 0)
    assert a.collect_med_reminders() == []
    clock(a, date(2027, 2, 1), 12, 0)
    r = say(a, llm, "Чем я лечил ангину?", intent="MED_SHOW", med={"query": "ангина"})
    assert r.text.startswith("🎩 Ангина, Сэр!\n\n📅 24 сентября 2026 — 28 сентября 2026 (5 дней)")
    assert "💊 Амоксициллин\n   500 мг · 3 раза в день · после еды · 7 дней" in r.text
    assert r.text.endswith("⚕️ Повторять лечение лучше после консультации с врачом, Сэр.")


def test_add_change_stop_drug(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    r = say(a, llm, "Врач добавил нурофен по 1 таблетке 2 раза в день", intent="MED_DRUG",
            med={"action": "add", "drugs": [{"name": "Нурофен", "dose": "1 таблетка", "per_day": 2}]})
    assert r.text.startswith("🎩 Добавил в лечение, Сэр!") and "💊 Нурофен" in r.text
    r = say(a, llm, "Амоксициллин пить не 3, а 2 раза в день", intent="MED_DRUG",
            med={"action": "change", "drug": "амоксициллин", "drugs": [{"name": "амоксициллин", "per_day": 2}]})
    assert "2 раза в день" in r.text and "⏰ 09:00, 21:00" in r.text
    r = say(a, llm, "Перестал пить нурофен", intent="MED_DRUG", med={"action": "stop", "drug": "нурофен"})
    assert r.text == "🎩 Как скажете, Сэр! Больше не напоминаю про Нурофен."


def test_drug_without_case_asks_illness(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Назначили мелатонин на ночь", intent="MED_DRUG",
            med={"action": "add", "drugs": [{"name": "мелатонин"}]})
    assert r.text == "🎩 Разумеется, Сэр! От чего это лечение?"
    r = say(a, llm, "Бессонница", intent="ANSWER", answer="Бессонница")
    assert "🤒 Бессонница" in r.text and "⏰ 22:00" in r.text


def test_allergy_warning(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "У меня аллергия на пенициллин", intent="MED_ALLERGY", med={"allergy": "пенициллин"})
    assert r.text == "🎩 Записал, Сэр! Буду иметь в виду.\n\n⚠️ Аллергии: Пенициллин"
    r = say(a, llm, "x", intent="MED_CASE",
            med={"illness": "Бронхит", "drugs": [{"name": "Пенициллин G", "per_day": 2}]})
    assert "⚠️ Внимание, Сэр: у вас записана аллергия — Пенициллин!" in r.text
    r = say(a, llm, "Аллергии на пенициллин больше нет", intent="MED_ALLERGY",
            med={"allergy": "пенициллин", "remove": True})
    assert r.text == "🎩 Готово, Сэр! Убрал из аллергий: Пенициллин"


def test_contacts(tmp_path):
    a, llm = make(tmp_path)
    r = say(a, llm, "Мой терапевт Иванова Анна Петровна, поликлиника №5, телефон 88121234567",
            intent="MED_CONTACT", med={"contact": {"name": "Иванова Анна Петровна", "specialty": "терапевт",
                                                   "place": "поликлиника №5"}})
    assert r.text == ("🎩 Записал врача, Сэр!\n\n👨‍⚕️ Иванова Анна Петровна — терапевт\n"
                      "   📞 +7 812 123-45-67\n   🏥 поликлиника №5")
    r = say(a, llm, "Какой телефон у моего терапевта?", intent="MED_CONTACT", med={"contact": {"name": "терапевт"}})
    assert "📞 +7 812 123-45-67" in r.text
    r = a.handle_callback("med:docs")
    assert "Иванова Анна Петровна" in r.text


def test_menu_and_views(tmp_path):
    a, llm = make(tmp_path)
    r = run(a.handle_text("🩺 Медкарта"))
    assert r.text.startswith("🎩 Сэр, медкарта пока пуста!")
    say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    say(a, llm, "x", intent="MED_ALLERGY", med={"allergy": "орехи"})
    r = run(a.handle_text("🩺 Медкарта"))
    assert r.text == "🎩 Медкарта, Сэр!\n\n⚠️ Аллергии: Орехи"
    assert r.buttons[0][0][0] == "🤒 Ангина · с 24 сен"
    assert r.buttons[-1] == [("⚠️ Аллергии", "med:all"), ("👨‍⚕️ Врачи", "med:docs")]
    card = a.handle_callback(r.buttons[0][0][1])
    assert card.buttons[0] == [("✅ Я выздоровел", f"med:rec:{a.med.cases()[0].id}")]
    card = a.handle_callback(card.buttons[0][0][1])
    assert "✅ Я выздоровел" not in str(card.buttons) and a.med.cases()[0].ended == date(2026, 9, 24)
    conf = a.handle_callback(card.buttons[0][0][1])        # ❌ Удалить
    assert conf.text == "🎩 Сэр, удалить из медкарты: Ангина?"
    done = a.handle_callback(conf.buttons[0][0][1])
    assert a.med.cases() == [] and done.text.startswith("🎩 Удалил из медкарты, Сэр!")


def test_same_illness_adds_to_open_case(tmp_path):
    a, llm = make(tmp_path)
    say(a, llm, TEXT, intent="MED_CASE", med=ANGINA)
    r = say(a, llm, "При ангине ещё назначили стрепсилс", intent="MED_CASE",
            med={"illness": "ангина", "drugs": [{"name": "Стрепсилс"}]})
    assert r.text.startswith("🎩 Дополнил медкарту, Сэр!") and len(a.med.cases()) == 1
    assert [d.name for d in a.med.cases()[0].drugs] == ["Амоксициллин", "Стрепсилс"]
