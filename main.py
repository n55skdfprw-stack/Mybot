"""Запуск Альфреда."""

import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from alfred import VERSION
from alfred.brain.brain import Brain
from alfred.brain.llm_client import GigaChatClient
from alfred.config import load_config
from alfred.core.alfred import Alfred
from alfred.database.db import Database
from alfred.database.repositories import ContextRepository, NoteRepository, TaskRepository, UserRepository
from alfred.handlers.telegram import build_dispatcher
from alfred.notifications.scheduler import build_scheduler
from alfred.database.schedule_repo import EventRepository, NotificationRepository, RuleRepository
from alfred.services.notes import NoteService
from alfred.services.schedule import ScheduleService
from alfred.services.tasks import TaskService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("alfred")


async def main() -> None:
    config = load_config()

    db = Database(config.database_path)
    db.migrate()
    user_id = UserRepository(db).ensure(config.owner_id, config.timezone.key, config.default_city)

    llm = GigaChatClient(config.gigachat_key, config.gigachat_model, config.gigachat_model_pro)
    if await llm.check():
        log.info("GigaChat доступен")
    else:
        log.error("GigaChat НЕ доступен с этого сервера! Проверьте ключ или хостинг.")

    alfred = Alfred(
        brain=Brain(llm),
        tasks=TaskService(TaskRepository(db), user_id),
        notes=NoteService(NoteRepository(db), user_id),
        schedule=ScheduleService(EventRepository(db), RuleRepository(db), NotificationRepository(db), user_id),
        context=ContextRepository(db),
        user_id=user_id,
        tz=config.timezone,
    )

    bot = Bot(token=config.telegram_token)
    await bot.set_my_commands([BotCommand(command="start", description="Меню Альфреда")])
    dp = build_dispatcher(alfred, config.owner_id)

    scheduler = build_scheduler(bot, alfred, config.owner_id)
    scheduler.start()

    log.info("Альфред запущен (версия %s). База: %s", VERSION, config.database_path)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
