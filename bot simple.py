"""
Личный ассистент в Telegram (простая версия): задачи, заметки, напоминания.

Полностью бесплатно — не использует никаких платных API.
Позже можно легко добавить умный чат на Claude (просто скажи Claude добавить это).

Всё хранится в одном файле assistant.db (SQLite), создаётся автоматически.
Бот отвечает только ТЕБЕ (по твоему Telegram user_id) — задаётся в .env.
"""
import functools

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")  # твой Telegram user_id, строкой
DB_PATH = os.path.join(os.path.dirname(__file__), "assistant.db")

if not TELEGRAM_TOKEN or not OWNER_ID:
    raise RuntimeError(
        "Не заданы переменные окружения. Проверь файл .env "
        "(TELEGRAM_TOKEN, OWNER_ID)."
    )

OWNER_ID = int(OWNER_ID)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("assistant-bot")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ---------- База данных ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT
        )"""
    )
    return conn


# ---------- Проверка владельца ----------

def owner_only(handler):
    @functools.wraps(handler)
    async def wrapper(message: Message, *args, **kwargs):
        if message.from_user.id != OWNER_ID:
            await message.answer("Этот бот приватный 🙂")
            return
        return await handler(message, *args, **kwargs)
    return wrapper


# ---------- Команды: задачи ----------

@dp.message(Command("task"))
@owner_only
async def add_task(message: Message):
    text = message.text.replace("/task", "", 1).strip()
    if not text:
        await message.answer("Использование: /task купить билеты")
        return
    conn = db()
    conn.execute(
        "INSERT INTO tasks (text, created_at) VALUES (?, ?)",
        (text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await message.answer(f"✅ Добавил задачу: {text}")


@dp.message(Command("tasks"))
@owner_only
async def list_tasks(message: Message):
    conn = db()
    rows = conn.execute(
        "SELECT id, text, done FROM tasks WHERE done = 0 ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        await message.answer("Активных задач нет 🎉")
        return
    lines = [f"{i}. {t}" for i, t, _ in rows]
    await message.answer(
        "📋 Твои задачи:\n" + "\n".join(lines) +
        "\n\nОтметить: /done <номер>"
    )


@dp.message(Command("done"))
@owner_only
async def done_task(message: Message):
    arg = message.text.replace("/done", "", 1).strip()
    if not arg.isdigit():
        await message.answer("Использование: /done 3")
        return
    conn = db()
    conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (int(arg),))
    conn.commit()
    conn.close()
    await message.answer("✅ Готово, задача закрыта")


# ---------- Команды: заметки ----------

@dp.message(Command("note"))
@owner_only
async def add_note(message: Message):
    text = message.text.replace("/note", "", 1).strip()
    if not text:
        await message.answer("Использование: /note идея для тренировки")
        return
    conn = db()
    conn.execute(
        "INSERT INTO notes (text, created_at) VALUES (?, ?)",
        (text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await message.answer("📝 Заметка сохранена")


@dp.message(Command("notes"))
@owner_only
async def list_notes(message: Message):
    conn = db()
    rows = conn.execute("SELECT text FROM notes ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    if not rows:
        await message.answer("Заметок пока нет")
        return
    lines = [f"• {t}" for (t,) in rows]
    await message.answer("🗒 Последние заметки:\n" + "\n".join(lines))


# ---------- Команда: напоминания ----------
# Формат: /remind 30m текст  ИЛИ  /remind 18:30 текст

@dp.message(Command("remind"))
@owner_only
async def add_reminder(message: Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "/remind 30m текст — через 30 минут\n"
            "/remind 2h текст — через 2 часа\n"
            "/remind 18:30 текст — сегодня в 18:30"
        )
        return
    _, when, text = parts
    run_at = None
    now = datetime.now()

    if when.endswith("m") and when[:-1].isdigit():
        run_at = now + timedelta(minutes=int(when[:-1]))
    elif when.endswith("h") and when[:-1].isdigit():
        run_at = now + timedelta(hours=int(when[:-1]))
    elif ":" in when:
        try:
            hh, mm = map(int, when.split(":"))
            run_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if run_at < now:
                run_at += timedelta(days=1)
        except ValueError:
            pass

    if not run_at:
        await message.answer("Не понял время. Примеры: 30m, 2h, 18:30")
        return

    scheduler.add_job(
        send_reminder, "date", run_date=run_at, args=[text]
    )
    await message.answer(f"⏰ Напомню: «{text}» — {run_at.strftime('%d.%m %H:%M')}")


async def send_reminder(text: str):
    await bot.send_message(OWNER_ID, f"⏰ Напоминание: {text}")


# ---------- Старт и общее ----------

@dp.message(CommandStart())
@owner_only
async def start(message: Message):
    await message.answer(
        "Привет! Я твой личный органайзер 🤖\n\n"
        "Команды:\n"
        "/task текст — добавить задачу\n"
        "/tasks — список задач\n"
        "/done N — закрыть задачу\n"
        "/note текст — сохранить заметку\n"
        "/notes — последние заметки\n"
        "/remind 30m текст — напомнить\n\n"
        "(Позже сюда можно добавить умный чат на Claude)"
    )


@dp.message(F.text)
@owner_only
async def fallback(message: Message):
    await message.answer(
        "Я пока умею только команды — набери /start, чтобы увидеть список."
    )


async def main():
    scheduler.start()
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
