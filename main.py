"""Запуск Альфреда."""

import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from alfred import VERSION
from alfred.brain.brain import Brain
from alfred.brain.llm_client import GigaChatClient
from alfred.config import load_config
from alfred.core.access import Access
from alfred.core.admin import Admin
from alfred.database.db import Database
from alfred.handlers.telegram import build_dispatcher
from alfred.notifications.scheduler import build_scheduler
from alfred.services.currency import CurrencyService
from alfred.services.weather import WeatherService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("alfred")


async def main() -> None:
    config = load_config()

    db = Database(config.database_path)
    db.migrate()

    llm = GigaChatClient(config.gigachat_key, config.gigachat_model, config.gigachat_model_pro)
    if await llm.check():
        log.info("GigaChat доступен")
    else:
        log.error("GigaChat НЕ доступен с этого сервера! Проверьте ключ или хостинг.")

    access = Access(db, Brain(llm), config.owner_id, config.timezone, config.default_city,
                    currency=CurrencyService(), weather=WeatherService())
    owner = access.setup_owner()
    log.info("Владелец: user_id=%s, пользователей всего: %s", owner.id, len(access.users.accounts()))

    rates = await access.currency.get()
    if rates:
        log.info("Курсы ЦБ доступны: доллар %.2f ₽ на %s", rates.rub_per_unit.get("USD", 0), rates.day)
    else:
        log.error("Курсы ЦБ НЕ доступны с этого сервера — пересчёт валют работать не будет.")

    bot = Bot(token=config.telegram_token)
    await bot.set_my_commands([BotCommand(command="start", description="🎩 Приветствую, Альфред!"),
                               BotCommand(command="pause", description="⏸ Пауза — Альфред замолчит"),
                               BotCommand(command="resume", description="▶️ Продолжить"),
                               BotCommand(command="clean", description="🧹 Чистый лист — удалить всё")])
    try:
        await bot.set_my_short_description("🎩 Альфред — ваш онлайн дворецкий: дела, финансы, медкарта, погода.")
        await bot.set_my_description("🎩 Добрый день! Я Альфред — ваш онлайн дворецкий.\n\n"
                                     "Веду дела и распорядок, заметки, финансы, дни рождения, досье и медкарту, "
                                     "подскажу погоду и напомню о важном.\n\nНажмите кнопку ниже, чтобы начать.")
    except Exception:
        log.exception("Не удалось обновить описание бота")
    dp = build_dispatcher(access, Admin(access, llm))

    scheduler = build_scheduler(bot, access)
    scheduler.start()

    log.info("Альфред запущен (версия %s). База: %s", VERSION, config.database_path)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
