"""Плановые задачи Альфреда.

По местному времени каждого человека (часовой пояс — по городу, где он сейчас: «Я в Новосибирске»):
- 8:00, 14:00, 18:00 — проверки дел (утром — вместе с погодой и советами);
  если первое дело раньше 9:00 — утренняя сводка приходит за час до него (утренняя пропускается, если сводка пришла с напоминанием о лекции);
- 12:00 — дни рождения (сегодня, завтра и через неделю — одним сообщением);
- 19:00 — расписание на завтра;
- 03:00 — продление повторяющихся событий.
Каждую минуту — напоминания о событиях распорядка и о приёме лекарств.
По Москве: 04:00 — резервная копия.

Всё это — для каждого, кому служит Альфред (владелец и приглашённые), у каждого — своё.

Если бот был выключен, пропущенные сообщения потом не присылаются (опоздание больше 5 минут).
"""

import logging
from datetime import timedelta
from typing import Awaitable, Callable

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..core.access import Access
from ..core.reply import Reply
from ..handlers.telegram import send_reply

log = logging.getLogger(__name__)

DAILY = {"check_morning": "08:00", "birthdays": "12:00", "check_day": "14:00", "check_evening": "18:00",
         "tomorrow": "19:00", "extend": "03:00"}
WINDOW = timedelta(minutes=5)       # бот был выключен дольше — эту рассылку сегодня уже не шлём

Send = Callable[[Reply], Awaitable[None]]


async def _check(period: str, alfred, send: Send) -> None:
    weather = await alfred.morning_weather() if period == "morning" else None
    reply = alfred.check_message(period, weather=weather)
    if reply:
        await send(reply)


async def _birthdays(alfred, send: Send) -> None:
    reply = alfred.birthday_reminder()
    if reply:
        await send(reply)


async def _tomorrow(alfred, send: Send) -> None:
    await send(alfred.tomorrow_summary())


async def _extend(alfred, send: Send) -> None:
    alfred.extend_schedule()


DAILY_JOBS = {
    "check_morning": lambda a, s: _check("morning", a, s),
    "check_day": lambda a, s: _check("day", a, s),
    "check_evening": lambda a, s: _check("evening", a, s),
    "birthdays": _birthdays,
    "tomorrow": _tomorrow,
    "extend": _extend,
}


async def for_each(access: Access, name: str, job) -> None:
    """Выполнить задачу для каждого пользователя. Ошибка у одного не мешает остальным."""
    for acc, alfred in access.active():
        try:
            async with access.lock(acc.id):      # не вклиниваемся посреди команды человека
                await job(acc, alfred)
        except Exception:
            log.exception("%s failed for user %s", name, acc.id)


async def daily_tick(access: Access, send_to: Callable[..., Awaitable[None]]) -> None:
    """Каждую минуту: у кого по ЕГО местному времени настал час сводки или проверки — тому и шлём (один раз в день)."""
    async def job(acc, alfred):
        now = alfred.now()
        today = now.date().isoformat()
        for name, hhmm in DAILY.items():
            h, m = map(int, hhmm.split(":"))
            start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            key = f"daily:{acc.id}:{name}"
            if not start <= now < start + WINDOW or access.users.setting(key) == today:
                continue
            access.users.set_setting(key, today)      # отмечаем заранее: даже при сбое второй раз за день не шлём
            try:
                await DAILY_JOBS[name](alfred, lambda reply: send_to(acc, reply))
            except Exception:
                log.exception("%s failed for user %s", name, acc.id)
    await for_each(access, "daily", job)


def build_scheduler(bot: Bot, access: Access, backup=None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=access.tz)
    common = {"misfire_grace_time": 300, "coalesce": True, "replace_existing": True}

    def send(acc, reply):
        return send_reply(bot, acc.telegram_id, reply, owner=acc.role == "owner", address=acc.address)

    async def run_daily():
        await daily_tick(access, send)

    async def run_reminders():
        async def job(acc, alfred):
            weather = await alfred.morning_weather() if alfred.morning_due() else None
            for notif_id, reply in alfred.collect_reminders(weather=weather):
                if reply is None:
                    alfred.mark_reminder(notif_id, sent=False)
                    continue
                try:
                    await send(acc, reply)
                except Exception:
                    # не отмечаем как отправленное — через минуту попробуем ещё раз (пока не опоздали)
                    log.warning("Напоминание %s не ушло, повторю через минуту", notif_id)
                    continue
                alfred.mark_reminder(notif_id, sent=True)
        await for_each(access, "reminders", job)

    async def run_med():
        async def job(acc, alfred):
            for reply in alfred.collect_med_reminders():
                try:
                    await send(acc, reply)
                except Exception:
                    log.warning("Напоминание о лекарстве не ушло, повторю через минуту")
                    alfred.med_send_failed(reply)
        await for_each(access, "med reminders", job)

    async def run_backup():
        if not backup or not backup.channel:
            return
        err = await backup.run()
        if err:
            try:        # о проблеме с копией владельцу стоит знать
                await bot.send_message(access.owner_tg, f"🎩 Сэр, ночная резервная копия не удалась!\n\n⚠️ {err}")
            except Exception:
                log.exception("Не удалось сообщить о сбое копии")

    scheduler.add_job(run_daily, "cron", minute="*", id="daily", **common)
    scheduler.add_job(run_reminders, "interval", seconds=60, id="reminders", **common)
    scheduler.add_job(run_med, "interval", seconds=60, id="med", **common)
    scheduler.add_job(run_backup, "cron", hour=4, minute=0, id="backup", **common)
    return scheduler
