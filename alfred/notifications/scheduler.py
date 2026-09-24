"""Плановые задачи Альфреда.

- 10:00, 14:00, 18:00 — проверки дел (утром — вместе с погодой и советами) (утренняя пропускается, если сводка пришла с напоминанием о лекции);
- каждую минуту — напоминания о событиях распорядка;
- каждую минуту — напоминания о приёме лекарств (медкарта);
- 12:00 — дни рождения (сегодня, завтра и через неделю — одним сообщением);
- 19:00 — расписание на завтра;
- 03:00 — продление повторяющихся событий.

Всё это — для каждого, кому служит Альфред (владелец и приглашённые), у каждого — своё.

Если бот был выключен, пропущенные сообщения потом не присылаются.
"""

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..core.access import Access
from ..handlers.telegram import send_reply

log = logging.getLogger(__name__)

CHECKS = {"morning": 10, "day": 14, "evening": 18}


def build_scheduler(bot: Bot, access: Access) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=access.tz)
    common = {"misfire_grace_time": 300, "coalesce": True, "replace_existing": True}

    async def each(name: str, job):
        """Выполнить задачу для каждого пользователя. Ошибка у одного не мешает остальным."""
        for acc, alfred in access.active():
            try:
                await job(acc, alfred)
            except Exception:
                log.exception("%s failed for user %s", name, acc.id)

    def send(acc, reply):
        return send_reply(bot, acc.telegram_id, reply, owner=acc.role == "owner", address=acc.address)

    async def run_check(period: str):
        async def job(acc, alfred):
            weather = await alfred.morning_weather() if period == "morning" else None
            reply = alfred.check_message(period, weather=weather)
            if reply:
                await send(acc, reply)
        await each(f"check {period}", job)

    async def run_reminders():
        async def job(acc, alfred):
            for notif_id, reply in alfred.collect_reminders():
                if reply is None:
                    alfred.mark_reminder(notif_id, sent=False)
                    continue
                await send(acc, reply)
                alfred.mark_reminder(notif_id, sent=True)
        await each("reminders", job)

    async def run_med():
        async def job(acc, alfred):
            for reply in alfred.collect_med_reminders():
                await send(acc, reply)
        await each("med reminders", job)

    async def run_tomorrow():
        async def job(acc, alfred):
            await send(acc, alfred.tomorrow_summary())
        await each("tomorrow summary", job)

    async def run_birthdays():
        async def job(acc, alfred):
            reply = alfred.birthday_reminder()
            if reply:
                await send(acc, reply)
        await each("birthdays", job)

    async def run_extend():
        async def job(acc, alfred):
            alfred.extend_schedule()
        await each("extend", job)

    for period, hour in CHECKS.items():
        scheduler.add_job(run_check, "cron", hour=hour, minute=0, args=[period], id=f"check_{period}", **common)
    scheduler.add_job(run_reminders, "interval", seconds=60, id="reminders", **common)
    scheduler.add_job(run_med, "interval", seconds=60, id="med", **common)
    scheduler.add_job(run_birthdays, "cron", hour=12, minute=0, id="birthdays", **common)
    scheduler.add_job(run_tomorrow, "cron", hour=19, minute=0, id="tomorrow", **common)
    scheduler.add_job(run_extend, "cron", hour=3, minute=0, id="extend", **common)
    return scheduler
