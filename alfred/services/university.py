"""Расписание занятий вуза (НГУ им. Лесгафта, timetable.lesgaft.spb.ru).

Сайт отдаёт расписание только после входа — логином и паролем «Дистанционного образования» студента.
Логин и пароль берутся ТОЛЬКО из переменных окружения Railway (UNI_LOGIN, UNI_PASSWORD) и никуда не пишутся:
ни в базу, ни в логи, ни в сообщения.
"""

import logging
from datetime import date
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

BASE_URL = "https://timetable.lesgaft.spb.ru"
LOGIN_URL = f"{BASE_URL}/api/timetable2/auth/login"
LOGOUT_URL = f"{BASE_URL}/api/timetable2/auth/logout"
SCHEDULE_URL = f"{BASE_URL}/api/timetable2/schedule"


class UniversityError(Exception):
    """wrong_password — логин или пароль не подходят; unavailable — сайт не отвечает или изменился."""


Fetch = Callable[[date, date], Awaitable[list[dict]]]


class UniversityClient:
    def __init__(self, login: str, password: str, group: str, fetch: Optional[Fetch] = None):
        self.group = group
        self._login = login
        self._password = password
        self._fetch = fetch or self._fetch_from_site

    async def lessons(self, start: date, end: date) -> list[dict]:
        """Пары группы с start по end включительно — как их отдаёт сайт (дата, время, предмет, аудитория…)."""
        items = await self._fetch(start, end)
        return [i for i in items if self.group in (i.get("groups") or [self.group])]

    async def _fetch_from_site(self, start: date, end: date) -> list[dict]:
        import aiohttp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.post(LOGIN_URL, json={"username": self._login, "password": self._password},
                                        headers={"Accept": "application/json"}) as resp:
                    if resp.status in (400, 401, 403):
                        raise UniversityError("wrong_password")
                    resp.raise_for_status()
                try:
                    params = {"from": start.isoformat(), "to": end.isoformat(), "group": self.group}
                    async with session.get(SCHEDULE_URL, params=params,
                                           headers={"Accept": "application/json"}) as resp:
                        if resp.status == 401:
                            raise UniversityError("wrong_password")
                        resp.raise_for_status()
                        data = await resp.json(content_type=None)
                finally:
                    try:
                        async with session.post(LOGOUT_URL):     # вежливо выходим — сессию на сайте не держим
                            pass
                    except Exception:
                        pass
        except UniversityError:
            raise
        except Exception as exc:
            # В лог — только тип ошибки: ни логина, ни пароля, ни ответа сайта
            log.warning("Сайт расписания вуза не ответил: %s", type(exc).__name__)
            raise UniversityError("unavailable") from exc
        items = (data or {}).get("items")
        if not isinstance(items, list):
            log.warning("Сайт расписания вуза ответил в неожиданном виде")
            raise UniversityError("unavailable")
        return items
