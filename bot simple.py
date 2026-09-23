"""
Личный ассистент в Telegram (версия с меню): задачи, заметки, напоминания, дни рождения.

Полностью бесплатно. Всё хранится в одном файле assistant.db (SQLite).
Бот отвечает только ТЕБЕ (по твоему Telegram user_id) — задаётся в .env.
"""

import asyncio
import calendar
import functools
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")
DB_PATH = os.path.join(os.path.dirname(__file__), "assistant.db")

if not TELEGRAM_TOKEN or not OWNER_ID:
    raise RuntimeError("Не заданы TELEGRAM_TOKEN / OWNER_ID в .env")

OWNER_ID = int(OWNER_ID)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("assistant-bot")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
             "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# ---------- FSM состояния ----------

class NoteStates(StatesGroup):
    waiting_text = State()

class TaskStates(StatesGroup):
    waiting_text = State()

class ReminderStates(StatesGroup):
    waiting_text = State()
    choosing_date = State()
    choosing_hour = State()
    choosing_minute = State()
    choosing_advance = State()

class BirthdayStates(StatesGroup):
    waiting_name = State()
    choosing_date = State()

# ---------- Клавиатуры ----------

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Заметки"), KeyboardButton(text="⏰ Напоминания")],
        [KeyboardButton(text="✅ Задачи"), KeyboardButton(text="🎂 Дни рождения")],
    ],
    resize_keyboard=True,
)


def section_menu(section: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📃 Список", callback_data=f"menu:{section}:list"),
        InlineKeyboardButton(text="➕ Добавить", callback_data=f"menu:{section}:add"),
    ]])


def build_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year

    rows = [[
        InlineKeyboardButton(text="«", callback_data=f"cal:nav:{prev_year}:{prev_month}"),
        InlineKeyboardButton(text=f"{MONTHS_RU[month-1]} {year}", callback_data="cal:ignore"),
        InlineKeyboardButton(text="»", callback_data=f"cal:nav:{next_year}:{next_month}"),
    ]]
    rows.append([InlineKeyboardButton(text=d, callback_data="cal:ignore")
                 for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]])

    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:ignore"))
            else:
                row.append(InlineKeyboardButton(
                    text=str(day), callback_data=f"cal:day:{year}:{month}:{day}"
                ))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_hour_keyboard() -> InlineKeyboardMarkup:
    hours = list(range(24))
    rows = [
        [InlineKeyboardButton(text=f"{h:02d}", callback_data=f"hour:{h}") for h in hours[i:i+6]]
        for i in range(0, 24, 6)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_minute_keyboard() -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=f"{m:02d}", callback_data=f"min:{m}") for m in (0, 15, 30, 45)]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def build_advance_keyboard() -> InlineKeyboardMarkup:
    options = [
        ("Точно в это время", 0), ("За 10 минут", 10), ("За 30 минут", 30),
        ("За 1 час", 60), ("За 3 часа", 180), ("За 1 день", 1440),
        ("За 3 дня", 4320), ("За неделю", 10080),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"adv:{v}")] for t, v in options
    ])


# ---------- База данных ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
        done INTEGER DEFAULT 0, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
        event_at TEXT NOT NULL, notify_at TEXT NOT NULL,
        notified INTEGER DEFAULT 0, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS birthdays (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        day INTEGER NOT NULL, month INTEGER NOT NULL, advance_days INTEGER DEFAULT 0,
        last_notified_year INTEGER DEFAULT 0, last_advance_notified_year INTEGER DEFAULT 0,
        created_at TEXT)""")
    return conn


# ---------- Проверки доступа ----------

def owner_only(handler):
    @functools.wraps(handler)
    async def wrapper(message: Message, *args, **kwargs):
        if message.from_user.id != OWNER_ID:
            await message.answer("Этот бот приватный 🙂")
            return
        return await handler(message, *args, **kwargs)
    return wrapper


def owner_only_cb(handler):
    @functools.wraps(handler)
    async def wrapper(callback: CallbackQuery, *args, **kwargs):
        if callback.from_user.id != OWNER_ID:
            await callback.answer("Приватный бот", show_alert=True)
            return
        return await handler(callback, *args, **kwargs)
    return wrapper


# ---------- Списки ----------

def tasks_list_content():
    conn = db()
    rows = conn.execute("SELECT id, text FROM tasks WHERE done=0 ORDER BY id").fetchall()
    conn.close()
    if not rows:
        return "Активных задач нет 🎉", None
    buttons = [
        [InlineKeyboardButton(
            text=f"✅ {t[:40] + '…' if len(t) > 40 else t}",
            callback_data=f"done:{i}",
        )]
        for i, t in rows
    ]
    return "Твои задачи (нажми, чтобы закрыть):", InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_tasks(target):
    text, markup = tasks_list_content()
    await target.answer(text, reply_markup=markup)


async def show_notes(target):
    conn = db()
    rows = conn.execute("SELECT text FROM notes ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    if not rows:
        await target.answer("Заметок пока нет")
        return
    lines = [f"• {t}" for (t,) in rows]
    await target.answer("📝 Последние заметки:\n" + "\n".join(lines))


async def show_reminders(target):
    conn = db()
    rows = conn.execute(
        "SELECT text, event_at FROM reminders WHERE notified=0 ORDER BY event_at"
    ).fetchall()
    conn.close()
    if not rows:
        await target.answer("Активных напоминаний нет")
        return
    lines = [f"• {t} — {datetime.fromisoformat(e).strftime('%d.%m %H:%M')}" for t, e in rows]
    await target.answer("⏰ Твои напоминания:\n" + "\n".join(lines))


async def show_birthdays(target):
    conn = db()
    rows = conn.execute(
        "SELECT name, day, month FROM birthdays ORDER BY month, day"
    ).fetchall()
    conn.close()
    if not rows:
        await target.answer("Дней рождения пока нет")
        return
    lines = [f"• {n} — {d:02d}.{m:02d}" for n, d, m in rows]
    await target.answer("🎂 Дни рождения:\n" + "\n".join(lines))


# ---------- Главное меню (кнопки) ----------

@dp.message(F.text == "📝 Заметки")
@owner_only
async def menu_notes(message: Message):
    await message.answer("Заметки:", reply_markup=section_menu("notes"))


@dp.message(F.text == "✅ Задачи")
@owner_only
async def menu_tasks(message: Message):
    await message.answer("Задачи:", reply_markup=section_menu("tasks"))


@dp.message(F.text == "⏰ Напоминания")
@owner_only
async def menu_reminders(message: Message):
    await message.answer("Напоминания:", reply_markup=section_menu("reminders"))


@dp.message(F.text == "🎂 Дни рождения")
@owner_only
async def menu_birthdays(message: Message):
    await message.answer("Дни рождения:", reply_markup=section_menu("birthdays"))


@dp.callback_query(F.data.startswith("menu:"))
@owner_only_cb
async def menu_callback(callback: CallbackQuery, state: FSMContext):
    _, section, action = callback.data.split(":")
    if action == "list":
        if section == "notes":
            await show_notes(callback.message)
        elif section == "tasks":
            await show_tasks(callback.message)
        elif section == "reminders":
            await show_reminders(callback.message)
        elif section == "birthdays":
            await show_birthdays(callback.message)
    elif action == "add":
        if section == "notes":
            await state.set_state(NoteStates.waiting_text)
            await callback.message.answer("Напиши текст заметки:")
        elif section == "tasks":
            await state.set_state(TaskStates.waiting_text)
            await callback.message.answer("Напиши текст задачи:")
        elif section == "reminders":
            await state.set_state(ReminderStates.waiting_text)
            await callback.message.answer("О чём напомнить? Напиши текст:")
        elif section == "birthdays":
            await state.set_state(BirthdayStates.waiting_name)
            await callback.message.answer("Чей день рождения? Напиши имя:")
    await callback.answer()


# ---------- Добавление заметки / задачи (просто текст) ----------

@dp.message(NoteStates.waiting_text)
@owner_only
async def note_text_received(message: Message, state: FSMContext):
    conn = db()
    conn.execute("INSERT INTO notes (text, created_at) VALUES (?, ?)",
                 (message.text, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("📝 Заметка сохранена", reply_markup=main_keyboard)


@dp.message(TaskStates.waiting_text)
@owner_only
async def task_text_received(message: Message, state: FSMContext):
    conn = db()
    conn.execute("INSERT INTO tasks (text, created_at) VALUES (?, ?)",
                 (message.text, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"✅ Добавил задачу: {message.text}", reply_markup=main_keyboard)


# ---------- Добавление напоминания (текст → дата → час → минуты → заранее) ----------

@dp.message(ReminderStates.waiting_text)
@owner_only
async def reminder_text_received(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(ReminderStates.choosing_date)
    today = datetime.now()
    await message.answer("Выбери дату:", reply_markup=build_calendar(today.year, today.month))


# ---------- Добавление дня рождения (имя → дата → заранее) ----------

@dp.message(BirthdayStates.waiting_name)
@owner_only
async def birthday_name_received(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(BirthdayStates.choosing_date)
    today = datetime.now()
    await message.answer("Выбери дату рождения:", reply_markup=build_calendar(today.year, today.month))


# ---------- Общие обработчики календаря ----------

@dp.callback_query(F.data == "cal:ignore")
async def cal_ignore(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("cal:nav:"))
@owner_only_cb
async def cal_nav(callback: CallbackQuery):
    _, _, y, m = callback.data.split(":")
    await callback.message.edit_reply_markup(reply_markup=build_calendar(int(y), int(m)))
    await callback.answer()


@dp.callback_query(F.data.startswith("cal:day:"))
@owner_only_cb
async def cal_day_chosen(callback: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    _, _, y, m, d = callback.data.split(":")
    y, m, d = int(y), int(m), int(d)

    if current == ReminderStates.choosing_date.state:
        await state.update_data(year=y, month=m, day=d)
        await state.set_state(ReminderStates.choosing_hour)
        await callback.message.edit_text(
            f"Дата: {d:02d}.{m:02d}.{y}\nВыбери час:", reply_markup=build_hour_keyboard()
        )
    elif current == BirthdayStates.choosing_date.state:
        data = await state.get_data()
        conn = db()
        conn.execute(
            "INSERT INTO birthdays (name, day, month, advance_days, created_at) VALUES (?,?,?,1,?)",
            (data["name"], d, m, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        await callback.message.edit_text(
            f"🎂 День рождения сохранён:\n{data['name']} — {d:02d}.{m:02d}\n"
            f"Напомню за 1 день до праздника 🔔"
        )
        await state.clear()
        await callback.message.answer("Готово!", reply_markup=main_keyboard)
    await callback.answer()


@dp.callback_query(ReminderStates.choosing_hour, F.data.startswith("hour:"))
@owner_only_cb
async def reminder_hour_chosen(callback: CallbackQuery, state: FSMContext):
    h = int(callback.data.split(":")[1])
    await state.update_data(hour=h)
    await state.set_state(ReminderStates.choosing_minute)
    await callback.message.edit_text(f"Час: {h:02d}\nВыбери минуты:", reply_markup=build_minute_keyboard())
    await callback.answer()


@dp.callback_query(ReminderStates.choosing_minute, F.data.startswith("min:"))
@owner_only_cb
async def reminder_minute_chosen(callback: CallbackQuery, state: FSMContext):
    m = int(callback.data.split(":")[1])
    await state.update_data(minute=m)
    await state.set_state(ReminderStates.choosing_advance)
    await callback.message.edit_text("За сколько напомнить заранее?", reply_markup=build_advance_keyboard())
    await callback.answer()


@dp.callback_query(ReminderStates.choosing_advance, F.data.startswith("adv:"))
@owner_only_cb
async def reminder_advance_chosen(callback: CallbackQuery, state: FSMContext):
    adv = int(callback.data.split(":")[1])
    data = await state.get_data()
    event_at = datetime(data["year"], data["month"], data["day"], data["hour"], data["minute"])
    notify_at = event_at - timedelta(minutes=adv)

    conn = db()
    conn.execute(
        "INSERT INTO reminders (text, event_at, notify_at, notified, created_at) VALUES (?,?,?,0,?)",
        (data["text"], event_at.isoformat(), notify_at.isoformat(), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    await callback.message.edit_text(
        f"⏰ Напоминание сохранено:\n«{data['text']}»\n{event_at.strftime('%d.%m.%Y %H:%M')}"
    )
    await state.clear()
    await callback.answer()
    await callback.message.answer("Готово!", reply_markup=main_keyboard)


# ---------- Фоновая проверка: напоминания и дни рождения ----------

async def check_reminders_and_birthdays():
    now = datetime.now()
    conn = db()

    rows = conn.execute(
        "SELECT id, text, event_at FROM reminders WHERE notified=0 AND notify_at <= ?",
        (now.isoformat(),),
    ).fetchall()
    for rid, text, event_at in rows:
        event_dt = datetime.fromisoformat(event_at)
        if event_dt <= now:
            msg = f"⏰ {text}"
        else:
            msg = f"⏰ Напоминание: {text}\n(событие {event_dt.strftime('%d.%m %H:%M')})"
        await bot.send_message(OWNER_ID, msg)
        conn.execute("UPDATE reminders SET notified=1 WHERE id=?", (rid,))
    conn.commit()

    rows = conn.execute(
        "SELECT id, name, day, month, advance_days, last_notified_year, last_advance_notified_year "
        "FROM birthdays"
    ).fetchall()
    today = now.date()
    for bid, name, day, month, adv, last_notified, last_adv in rows:
        try:
            this_year_date = datetime(today.year, month, day).date()
        except ValueError:
            continue
        advance_date = this_year_date - timedelta(days=adv)
        if adv > 0 and today == advance_date and last_adv != today.year:
            await bot.send_message(OWNER_ID, f"🎂 Через {adv} дн. день рождения: {name}")
            conn.execute("UPDATE birthdays SET last_advance_notified_year=? WHERE id=?", (today.year, bid))
        if today == this_year_date and last_notified != today.year:
            await bot.send_message(OWNER_ID, f"🎉 Сегодня день рождения: {name}!")
            conn.execute("UPDATE birthdays SET last_notified_year=? WHERE id=?", (today.year, bid))
    conn.commit()
    conn.close()


# ---------- Старые команды (на всякий случай, для быстрого доступа) ----------

@dp.message(Command("tasks"))
@owner_only
async def cmd_tasks(message: Message):
    await show_tasks(message)


@dp.message(Command("notes"))
@owner_only
async def cmd_notes(message: Message):
    await show_notes(message)


@dp.message(Command("done"))
@owner_only
async def cmd_done(message: Message):
    arg = message.text.replace("/done", "", 1).strip()
    if not arg.isdigit():
        await message.answer("Использование: /done 3")
        return
    conn = db()
    conn.execute("UPDATE tasks SET done=1 WHERE id=?", (int(arg),))
    conn.commit()
    conn.close()
    await message.answer("✅ Готово, задача закрыта")


@dp.callback_query(F.data.startswith("done:"))
@owner_only_cb
async def task_done_callback(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    conn = db()
    conn.execute("UPDATE tasks SET done=1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    text, markup = tasks_list_content()
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback.answer("Закрыта ✅")


# ---------- Старт ----------

@dp.message(CommandStart())
@owner_only
async def start(message: Message, state: FSMContext = None):
    if state:
        await state.clear()
    await message.answer(
        "Привет! Я твой личный органайзер 🤖\n\nВыбери раздел кнопками внизу:",
        reply_markup=main_keyboard,
    )


@dp.message(F.text)
@owner_only
async def fallback(message: Message):
    await message.answer("Не понял. Набери /start или воспользуйся кнопками ниже.", reply_markup=main_keyboard)


async def main():
    scheduler.add_job(check_reminders_and_birthdays, "interval", minutes=1)
    scheduler.start()
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
