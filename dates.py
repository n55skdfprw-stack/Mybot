"""Плановые задачи Альфреда.

- 10:00, 14:00, 18:00 — проверки дел (утренняя пропускается, если сводка пришла с напоминанием о лекции);
- каждую минуту — напоминания о событиях распорядка;
- 19:00 — расписание на завтра;
- 03:00 — продление повторяющихся событий.

Если бот был выключен, пропущенные сообщения потом не присылаются.
"""

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..core.alfred import Alfred
from ..handlers.telegram import send_reply

log = logging.getLogger(__name__)

CHECKS = {"morning": 10, "day": 14, "evening": 18}


def build_scheduler(bot: Bot, alfred: Alfred, owner_id: int) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=alfred.tz)
    common = {"misfire_grace_time": 300, "coalesce": True, "replace_existing": True}

    async def run_check(period: str):
        try:
            reply = alfred.check_message(period)
            if reply:
                await send_reply(bot, owner_id, reply)
        except Exception:
            log.exception("Check %s failed", period)

    async def run_reminders():
        try:
            for notif_id, reply in alfred.collect_reminders():
                if reply is None:
                    alfred.mark_reminder(notif_id, sent=False)
                    continue
                await send_reply(bot, owner_id, reply)
                alfred.mark_reminder(notif_id, sent=True)
        except Exception:
            log.exception("Reminders failed")

    async def run_tomorrow():
        try:
            await send_reply(bot, owner_id, alfred.tomorrow_summary())
        except Exception:
            log.exception("Tomorrow summary failed")

    async def run_extend():
        try:
            alfred.extend_schedule()
        except Exception:
            log.exception("Extend failed")

    for period, hour in CHECKS.items():
        scheduler.add_job(run_check, "cron", hour=hour, minute=0, args=[period], id=f"check_{period}", **common)
    scheduler.add_job(run_reminders, "interval", seconds=60, id="reminders", **common)
    scheduler.add_job(run_tomorrow, "cron", hour=19, minute=0, id="tomorrow", **common)
    scheduler.add_job(run_extend, "cron", hour=3, minute=0, id="extend", **common)
    return scheduler
