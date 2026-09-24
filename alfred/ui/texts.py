"""Тексты и оформление в стиле Альфреда. Предложения — с «!», вопросы — с «?»."""

import random
from datetime import date, datetime

from ..database.repositories import Note, Task

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]

# Кнопки главного меню
MENU_TASKS = "📋 Ваши дела"
MENU_SCHEDULE = "🕰️ Ваш распорядок"
MENU_NOTES = "📝 Ваши заметки"
MENU_FINANCE = "💰 Ваши финансы"
MENU_BIRTHDAYS = "🎂 Дни рождения"
MENU_DOSSIER = "🗂️ Досье"
MENU_WEATHER = "🌤 Погода"
MENU_MED = "🩺 Медкарта"
MENU_USERS = "👥 Пользователи"   # только у владельца
MENU_SYSTEM = "⚙️ Система"       # только у владельца
INVITE_ONLY = ("🎩 Прошу прощения, я личный дворецкий и служу только по приглашению.\n\n"
               "Если вас пригласили — проверьте, что в Telegram у вас указано то же @имя, "
               "которое назвали моему хозяину.")
BLOCKED = "🎩 Прошу прощения, доступ к Альфреду сейчас закрыт."
ASK_ADDRESS = "🎩 Добро пожаловать! Позвольте уточнить: как мне к вам обращаться?"
INTRO = ("🎩 Благодарю, {addr}!\n\nИтак, я ваш онлайн дворецкий, Альфред!\n\nВот чем могу служить:\n"
         "📋 дела и 🕰️ распорядок дня — напомню вовремя\n📝 заметки\n💰 расходы, доходы и долги\n"
         "🎂 дни рождения — никого не забудете поздравить\n🗂️ досье на близких и знакомых\n"
         "🩺 медкарта — болезни, лекарства и напоминания о приёме\n🌤 погода и советы на день\n\n"
         "Пишите мне обычными словами, например: «Завтра в 10 встреча с Сергеем». Разделы — в меню внизу.")
INTRO_LIMIT = "\n\nВ день я отвечаю на {limit} сообщений, кнопки меню — без ограничений."
PAUSE_WORDS = ("пауза", "альфред пауза", "альфред, пауза", "поставь на паузу", "замолчи", "стоп альфред",
               "альфред стоп", "/pause")
RESUME_WORDS = ("продолжить", "продолжай", "альфред продолжай", "альфред, продолжай", "сними с паузы",
                "сними паузу", "/resume", "▶️ продолжить")
PAUSED = ("🎩 Как скажете, Сэр! Альфред на паузе ⏸\n\nЯ не буду ничего писать: ни сводок, ни напоминаний "
          "(в том числе о лекарствах), и не буду отвечать на сообщения.\n\nЧтобы вернуть меня — нажмите "
          "«▶️ Продолжить» ниже или в синей кнопке «Меню».")
RESUMED = "🎩 Я снова с вами, Сэр! Сводки и напоминания включены."
START_WORDS = ("приветствую, альфред", "приветствую альфред")
WELCOME = ("🎩 Добро пожаловать, Сэр! Меня зовут Альфред, я ваш личный дворецкий.\n\n"
           "Я веду дела и распорядок дня, заметки, финансы и долги, дни рождения, досье и медкарту, "
           "подскажу погоду и напомню о важном.\n\n"
           "Пишите мне обычными словами, например: «Завтра купить молоко» или «Потратил 300 на такси». "
           "Разделы — в меню внизу.\n\nВ день я отвечаю на {limit} сообщений, кнопки меню — без ограничений.")
MENU_BUTTONS = [MENU_TASKS, MENU_SCHEDULE, MENU_NOTES, MENU_FINANCE, MENU_BIRTHDAYS, MENU_DOSSIER, MENU_WEATHER,
                MENU_MED]

CANCEL_WORDS = {"отмена", "отмени", "отменить", "стоп", "cancel"}


def pick(*options: str) -> str:
    return random.choice(options)


def day_greeting(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 12:
        return "Доброе утро"
    if 12 <= h < 17:
        return "Добрый день"
    if 17 <= h < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def greeting(now: datetime) -> str:
    g = day_greeting(now)
    return pick(
        f"🎩 {g}, Сэр!\nЧем могу услужить?",
        f"🎩 {g}!\nЧем могу услужить, Сэр?",
    )


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def due_label(d: date | None, today: date) -> str:
    """Подпись даты в списке дел (без «!»)."""
    if d is None:
        return ""
    delta = (d - today).days
    if delta < 0:
        return f" — {human_date(d)} (просрочено)"
    if delta == 0:
        return " — сегодня"
    if delta == 1:
        return " — завтра"
    if delta == 2:
        return " — послезавтра"
    if delta < 7:
        return f" — {WEEKDAYS_ACC[d.weekday()]}, {human_date(d)}"
    return f" — {human_date(d)}"


def due_phrase(d: date | None, today: date) -> str:
    """Дата внутри предложения: «на завтра», «на пятницу, 26 сентября»."""
    if d is None:
        return "без даты"
    delta = (d - today).days
    if delta == 0:
        return "на сегодня"
    if delta == 1:
        return "на завтра"
    if delta == 2:
        return "на послезавтра"
    if 0 < delta < 7:
        return f"на {WEEKDAYS_ACC[d.weekday()]}, {human_date(d)}"
    return f"на {human_date(d)}"


def task_line(t: Task, today: date, done: bool = False) -> str:
    box = "🟢" if done else "⭕"
    title = t.title[:1].upper() + t.title[1:]
    return f"{box} {title}{due_label(t.due_date, today)}"


def short(text: str, n: int = 40) -> str:
    text = text[:1].upper() + text[1:]
    return text if len(text) <= n else text[: n - 1] + "…"


def note_line(n: Note) -> str:
    return f"📝 {n.content}"


# --- Готовые фразы ---
SECTION_NOT_READY = "🎩 Этот раздел я ещё обустраиваю, Сэр! Совсем скоро смогу помочь и с этим!"
NOT_UNDERSTOOD = "🎩 Прошу прощения, Сэр, я не совсем понял! Можете сформулировать иначе?"
AI_UNAVAILABLE = "🎩 Сэр, сервис временно недоступен! Попробуйте, пожалуйста, чуть позже!"
DB_ERROR = "🎩 Сэр, не удалось выполнить это действие! Данные не изменены!"
CANCELLED = "🎩 Как прикажете, Сэр! Отменил!"
NOTHING_TO_CANCEL = "🎩 Отменять нечего, Сэр! Я к вашим услугам!"
THANKS = ("🎩 К вашим услугам, Сэр!", "🎩 Всегда рад помочь, Сэр!", "🎩 Не стоит благодарности, Сэр!")
