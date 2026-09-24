"""Раздел «🌤 Погода»: погода по часам и по дням, смена города, погода в утренней сводке, время в городах."""

import re
from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ..brain.dates import find_dates
from ..brain.parser import BrainResult
from ..database.repositories import UserRepository
from ..services import search
from ..services.weather import HOME, SPB, Place, WeatherService, WeatherUnavailable
from ..ui import weather_texts as W
from .reply import Reply

WEATHER_INTENTS = {"SHOW_WEATHER", "SET_CITY", "TIME_IN_CITY"}
HOME_WORDS = re.compile(r"\bдома\b|\bдомой\b|\bпитер|\bпетербург|\bспб\b")
DAYS_WORDS = re.compile(r"по дням|на недел|несколько дней|на выходн|на \d+ дн")
UNAVAILABLE = "🎩 Прошу прощения, Сэр! Не удалось узнать погоду. Попробуйте чуть позже!"
HOURS_AHEAD = 12


class WeatherMixin:
    weather: WeatherService
    users: UserRepository

    # ------------------------------------------------------------ город
    def _city(self) -> str:
        return self.users.city(self.user_id) or HOME

    async def _place(self, name: Optional[str]) -> Optional[Place]:
        """Ищем город. «Москве», «Казани», «Питере» — пробуем и начальную форму."""
        if not name:
            return SPB if self._city() == HOME else await self.weather.find_place(self._city())
        word = name.strip()
        tries = [word]
        if len(word) > 3:
            tries += [word[:-1] + "а", word[:-1] + "ь", word[:-1] + "я", word[:-1]]
        for candidate in dict.fromkeys(tries):
            place = await self.weather.find_place(candidate)
            if place:
                return place
        return None

    @staticmethod
    def _wx_buttons(city: Optional[str], mode: str = "n") -> list:
        """Две кнопки — на два других вида: «Сейчас», «По часам», «По дням»."""
        suffix = f":{city}" if city and len(f"wx:h:{city}".encode()) <= 60 else ""
        views = [("n", "🌤 Сейчас"), ("h", "🕐 По часам"), ("d", "📅 По дням")]
        return [[(label, f"wx:{m}{suffix}") for m, label in views if m != mode]]

    # ------------------------------------------------------------ экраны
    async def weather_view(self, city: Optional[str] = None, mode: str = "n", shift: int = 0,
                           edit: bool = False) -> Reply:
        """mode: n — коротко «сейчас и на день», h — по часам, d — по дням."""
        try:
            place = await self._place(city)
            if not place:
                return Reply(f"🎩 Сэр, не нашёл город «{city}»!", edit=edit)
            fc = await self.weather.forecast(place)
        except WeatherUnavailable:
            return Reply(UNAVAILABLE, edit=edit)
        buttons = self._wx_buttons(city, mode)
        today = fc.now.date()

        if mode == "d":
            lines = "\n".join(W.day_line(d, today) for d in fc.days)
            return Reply(f"🎩 Погода по дням, Сэр!\n\n📍 {place.name}\n\n{lines}", buttons=buttons, edit=edit)

        if shift > 0:
            day = today + timedelta(days=shift)
            d = fc.day(day)
            if not d:
                return Reply("🎩 Сэр, так далеко прогноз пока не заглядывает!", edit=edit)
            title = "на завтра" if shift == 1 else f"на {W.day_name(day, today).lower()}"
            text = (f"🎩 Погода {title}, Сэр!\n\n📍 {place.name} · ↑{W.t(d.t_max)} ↓{W.t(d.t_min)}\n"
                    f"{W.icon(d.code)} {W.desc(d.code).capitalize()}")
            tips = W.advice(fc.hours_of(day), d)
            return Reply(text + ("\n\n" + "\n".join(tips) if tips else ""), buttons=buttons, edit=edit)

        start = fc.now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if mode == "h":
            ahead = [h for h in fc.hours if h.time >= start][:HOURS_AHEAD]
            return Reply(f"🎩 Погода по часам, Сэр!\n\n📍 {place.name}\n\n"
                         + "\n".join(W.hour_line(h) for h in ahead), buttons=buttons, edit=edit)
        text = f"🎩 Погода, Сэр!\n\n{W.header(fc)}\n{W.now_block(fc)}"
        tips = W.advice(fc.hours_of(today, start), fc.day(today), rain_now=fc.code in W.RAIN)
        return Reply(text + ("\n\n" + "\n".join(tips) if tips else ""), buttons=buttons, edit=edit)

    async def morning_weather(self) -> Optional[str]:
        """Блок погоды для утренней сводки. None — если погоду узнать не удалось."""
        try:
            place = await self._place(None)
            fc = await self.weather.forecast(place)
        except WeatherUnavailable:
            return None
        today = fc.day(fc.now.date())
        top = f", днём до {W.t(today.t_max)}" if today else ""
        gusts = f", порывы до {round(fc.gusts)} м/с" if fc.gusts >= 12 else ""
        lines = [f"{W.icon(fc.code, not fc.is_day)} Погода: {place.name}",
                 f"Сейчас {W.t(fc.temp)}{top}, {W.desc(fc.code)}",
                 f"💨 Ветер {round(fc.wind)} м/с{gusts}"]
        tips = W.advice(fc.hours_of(fc.now.date(), fc.now), today, rain_now=fc.code in W.RAIN)
        return "\n".join(lines) + ("\n\n" + "\n".join(tips) if tips else "")

    # ------------------------------------------------------------ сообщения
    def _weather_shift(self) -> tuple[str, int]:
        text = search.normalize(self._raw_text or self._message or "")
        if DAYS_WORDS.search(text):
            return "d", 0
        if re.search(r"по часам|почасов", text):
            return "h", 0
        found = find_dates(self._raw_text or self._message or "", self.today())
        if found:
            shift = (found[0] - self.today()).days
            if 0 <= shift <= 6:
                return ("n" if shift == 0 else "h"), shift
        return "n", 0

    async def _weather_intent(self, r: BrainResult) -> Reply:
        if r.intent == "SHOW_WEATHER":
            mode, shift = self._weather_shift()
            return await self.weather_view(r.city, mode, shift)
        if r.intent == "SET_CITY":
            return await self._set_city(r)
        return await self._time_in_city(r)

    async def _set_city(self, r: BrainResult) -> Reply:
        said = search.normalize(self._raw_text or "")
        city = (r.city or "").strip()
        if not city or city.upper() == "HOME" or HOME_WORDS.search(said) or HOME_WORDS.search(city.lower()):
            self.users.set_city(self.user_id, None)
            return Reply(f"🎩 С возвращением, Сэр!\n\n📍 Погода снова для: {HOME}")
        try:
            place = await self._place(city)
        except WeatherUnavailable:
            return Reply(UNAVAILABLE)
        if not place:
            return Reply(f"🎩 Сэр, не нашёл город «{city}»!")
        self.users.set_city(self.user_id, place.name)
        return Reply(f"🎩 Как скажете, Сэр!\n\n📍 Теперь погода и утренняя сводка — для: {place.name}\n"
                     "Когда вернётесь, напишите «Я дома».")

    async def _time_in_city(self, r: BrainResult) -> Reply:
        if not r.city:
            return Reply(f"🎩 Разумеется, Сэр!\n\n{W.city_time(self._city(), self.now(), self.now())}")
        try:
            place = await self._place(r.city)
        except WeatherUnavailable:
            return Reply(UNAVAILABLE)
        if not place:
            return Reply(f"🎩 Сэр, не нашёл город «{r.city}»!")
        there = self.now().astimezone(ZoneInfo(place.timezone))
        return Reply(f"🎩 Разумеется, Сэр!\n\n{W.city_time(place.name, there, self.now())}")

    async def weather_callback(self, data: str) -> Reply:
        parts = data.split(":", 2)
        mode = parts[1] if len(parts) > 1 and parts[1] in ("n", "h", "d") else "n"
        city = parts[2] if len(parts) > 2 else None
        return await self.weather_view(city, mode, 0, edit=True)
