"""Курсы валют Центробанка РФ (cbr-xml-daily.ru). Ключ не нужен. Кэш на 3 часа."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

URL = "https://www.cbr-xml-daily.ru/daily_json.js"


@dataclass
class Rates:
    day: date
    rub_per_unit: dict[str, float]      # сколько рублей стоит 1 единица валюты
    prev_rub_per_unit: dict[str, float]

    def to_rub(self, amount: float, code: str) -> Optional[float]:
        if code == "RUB":
            return amount
        rate = self.rub_per_unit.get(code)
        return round(amount * rate, 2) if rate else None

    def convert(self, amount: float, src: str, dst: str) -> Optional[float]:
        rub = self.to_rub(amount, src)
        if rub is None:
            return None
        if dst == "RUB":
            return rub
        rate = self.rub_per_unit.get(dst)
        return round(rub / rate, 2) if rate else None


def parse_cbr(data: dict) -> Rates:
    values, prev = {}, {}
    for code, v in data["Valute"].items():
        nominal = v.get("Nominal", 1) or 1
        values[code] = v["Value"] / nominal
        prev[code] = v.get("Previous", v["Value"]) / nominal
    return Rates(date.fromisoformat(data["Date"][:10]), values, prev)


async def _fetch_json() -> dict:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(URL) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


class CurrencyService:
    def __init__(self, fetch: Optional[Callable[[], Awaitable[dict]]] = None, ttl_hours: int = 3):
        self._fetch = fetch or _fetch_json
        self._ttl = timedelta(hours=ttl_hours)
        self._cached: Optional[Rates] = None
        self._at: Optional[datetime] = None

    async def get(self) -> Optional[Rates]:
        if self._cached and self._at and datetime.now() - self._at < self._ttl:
            return self._cached
        try:
            self._cached = parse_cbr(await self._fetch())
            self._at = datetime.now()
        except Exception:
            log.exception("Не удалось получить курсы ЦБ")
        return self._cached  # если сеть недоступна — последний известный курс (или None)
