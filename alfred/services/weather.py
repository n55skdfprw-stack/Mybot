"""Погода и время в городах через Open-Meteo (open-meteo.com).

Бесплатно и без ключа: прогноз по часам на 7 дней, поиск города по названию (на русском)
и часовой пояс города. Результаты кэшируются, чтобы не дёргать сервис на каждое нажатие.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOME = "Санкт-Петербург"


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    timezone: str
    country: str = ""


SPB = Place(HOME, 59.9386, 30.3141, "Europe/Moscow", "Россия")


@dataclass
class Hour:
    time: datetime
    temp: float
    code: int
    rain_chance: int
    wind: float = 0.0


@dataclass
class Day:
    day: date
    code: int
    t_max: float
    t_min: float
    rain_chance: int
    wind_max: float
    gusts_max: float


@dataclass
class Forecast:
    place: Place
    now: datetime            # время в городе прогноза
    temp: float
    feels: float
    code: int
    wind: float
    gusts: float
    is_day: bool
    hours: list[Hour]
    days: list[Day]

    def hours_of(self, d: date, start: Optional[datetime] = None) -> list[Hour]:
        return [h for h in self.hours if h.time.date() == d and (start is None or h.time >= start)]

    def day(self, d: date) -> Optional[Day]:
        return next((x for x in self.days if x.day == d), None)


def parse_forecast(place: Place, data: dict) -> Forecast:
    cur = data["current"]
    h = data["hourly"]
    d = data["daily"]
    hours = [Hour(datetime.fromisoformat(t), temp, code or 0, int(p or 0), w or 0)
             for t, temp, code, p, w in zip(h["time"], h["temperature_2m"], h["weather_code"],
                                            h["precipitation_probability"], h["wind_speed_10m"])]
    days = [Day(date.fromisoformat(t), code or 0, tmax, tmin, int(p or 0), w or 0, g or 0)
            for t, code, tmax, tmin, p, w, g in zip(d["time"], d["weather_code"], d["temperature_2m_max"],
                                                    d["temperature_2m_min"], d["precipitation_probability_max"],
                                                    d["wind_speed_10m_max"], d["wind_gusts_10m_max"])]
    return Forecast(place, datetime.fromisoformat(cur["time"]), cur["temperature_2m"],
                    cur["apparent_temperature"], cur["weather_code"] or 0, cur["wind_speed_10m"] or 0,
                    cur["wind_gusts_10m"] or 0, bool(cur.get("is_day", 1)), hours, days)


async def _fetch_json(url: str, params: dict) -> dict:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


class WeatherUnavailable(Exception):
    pass


class WeatherService:
    def __init__(self, fetch: Optional[Callable[[str, dict], Awaitable[dict]]] = None, ttl_minutes: int = 20):
        self._fetch = fetch or _fetch_json
        self._ttl = timedelta(minutes=ttl_minutes)
        self._places: dict[str, Optional[Place]] = {}
        self._forecasts: dict[str, tuple[datetime, Forecast]] = {}

    async def find_place(self, name: str) -> Optional[Place]:
        """«Москва», «Токио», «Сочи» → место с координатами и часовым поясом. None — не нашли."""
        key = name.strip().lower().replace("ё", "е")
        if key in ("санкт-петербург", "петербург", "питер", "спб", "санкт петербург"):
            return SPB
        if key in self._places:
            return self._places[key]
        try:
            data = await self._fetch(GEO_URL, {"name": name.strip(), "count": 1, "language": "ru", "format": "json"})
        except Exception as e:
            log.warning("Geocoding failed: %s", e)
            raise WeatherUnavailable() from e
        results = data.get("results") or []
        place = None
        if results:
            r = results[0]
            place = Place(r["name"], r["latitude"], r["longitude"], r.get("timezone") or "UTC", r.get("country", ""))
        self._places[key] = place
        return place

    async def forecast(self, place: Place) -> Forecast:
        key = f"{place.lat:.3f},{place.lon:.3f}"
        cached = self._forecasts.get(key)
        if cached and datetime.now() - cached[0] < self._ttl:
            return cached[1]
        params = {
            "latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": 7,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m,is_day",
            "hourly": "temperature_2m,weather_code,precipitation_probability,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "wind_speed_10m_max,wind_gusts_10m_max",
        }
        try:
            fc = parse_forecast(place, await self._fetch(FORECAST_URL, params))
        except Exception as e:
            log.warning("Forecast failed: %s", e)
            raise WeatherUnavailable() from e
        self._forecasts[key] = (datetime.now(), fc)
        return fc
