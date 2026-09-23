"""Плановые проверки дел: 10:00, 14:00, 18:00 по времени пользователя.

Если бот был выключен в момент проверки — пропущенное сообщение не отправляется позже
(misfire_grace_time), чтобы не засыпать старыми уведомлениями.
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

    async def run_check(period: str):
        try:
            reply = alfred.check_message(period)
            if reply:
                await send_reply(bot, owner_id, reply)
        except Exception:
            log.exception("Check %s failed", period)

    for period, hour in CHECKS.items():
        scheduler.add_job(
            run_check, "cron", hour=hour, minute=0, args=[period],
            id=f"check_{period}", replace_existing=True,
            misfire_grace_time=300, coalesce=True,
        )
    return scheduler
