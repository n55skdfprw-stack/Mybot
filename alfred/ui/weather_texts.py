"""Тексты погоды: значки, строки по часам и по дням, советы Альфреда (по правилам, без ИИ)."""

import math
from datetime import date, datetime
from typing import Optional

from ..services.weather import Day, Forecast, Hour
from .texts import MONTHS_GEN

CODES = {
    0: ("☀️", "ясно"), 1: ("🌤", "почти ясно"), 2: ("⛅", "переменная облачность"), 3: ("☁️", "пасмурно"),
    45: ("🌫", "туман"), 48: ("🌫", "туман с изморозью"),
    51: ("🌦", "лёгкая морось"), 53: ("🌦", "морось"), 55: ("🌧", "сильная морось"),
    56: ("🌧", "ледяная морось"), 57: ("🌧", "ледяная морось"),
    61: ("🌦", "небольшой дождь"), 63: ("🌧", "дождь"), 65: ("🌧", "сильный дождь"),
    66: ("🌧", "ледяной дождь"), 67: ("🌧", "сильный ледяной дождь"),
    71: ("🌨", "небольшой снег"), 73: ("🌨", "снег"), 75: ("❄️", "сильный снег"), 77: ("🌨", "снежная крупа"),
    80: ("🌦", "небольшой ливень"), 81: ("🌧", "ливень"), 82: ("⛈", "сильный ливень"),
    85: ("🌨", "снегопад"), 86: ("❄️", "сильный снегопад"),
    95: ("⛈", "гроза"), 96: ("⛈", "гроза с градом"), 99: ("⛈", "сильная гроза с градом"),
}
RAIN = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
SNOW = {71, 73, 75, 77, 85, 86}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def icon(code: int, night: bool = False) -> str:
    if night and code in (0, 1):
        return "🌙"
    return CODES.get(code, ("🌡", ""))[0]


def desc(code: int) -> str:
    return CODES.get(code, ("", "без осадков"))[1]


def t(value: float) -> str:
    v = math.floor(value + 0.5)  # 12.5 → 13, как на градуснике, а не «банковское» округление
    return f"+{v}°" if v > 0 else (f"−{-v}°" if v < 0 else "0°")


def _night(h: datetime) -> bool:
    return h.hour >= 21 or h.hour < 6


def hour_line(h: Hour) -> str:
    rain = f" · 💧{h.rain_chance}%" if h.rain_chance >= 30 else ""
    return f"{h.time:%H:%M} {icon(h.code, _night(h.time))} {t(h.temp)}{rain}"


def day_name(d: date, today: date) -> str:
    shift = (d - today).days
    if shift == 0:
        return "Сегодня"
    if shift == 1:
        return "Завтра"
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS_GEN[d.month - 1][:3]}"


def day_line(x: Day, today: date) -> str:
    rain = f" · 💧{x.rain_chance}%" if x.rain_chance >= 30 else ""
    return f"{icon(x.code)} {day_name(x.day, today)}: {t(x.t_max)} / {t(x.t_min)}{rain}"


def advice(hours: list[Hour], day: Optional[Day], rain_now: bool = False) -> list[str]:
    """Советы только когда они нужны: зонт, снег, гололёд, ветер, жара, «оденьтесь слоями»."""
    out: list[str] = []
    rainy = [h for h in hours if h.code in RAIN or h.rain_chance >= 50]
    snowy = [h for h in hours if h.code in SNOW]
    if rain_now:
        out.append("🌧 Сейчас идёт дождь!")
    elif rainy:
        out.append(f"🌧 После {rainy[0].time:%H:%M} ожидается дождь!")
    if snowy:
        out.append(f"❄️ После {snowy[0].time:%H:%M} ожидается снег!")
    if day:
        if day.wind_max >= 10 or day.gusts_max >= 15:
            out.append(f"💨 Сильный ветер: до {round(max(day.wind_max, day.gusts_max))} м/с!")
    tips: list[str] = []
    if rain_now or rainy:
        tips.append("☂️ Позвольте напомнить, Сэр: возьмите зонт!")
    if snowy:
        tips.append("🥾 Наденьте тёплую обувь!")
    if day and day.t_min <= 1:
        tips.append("🧊 Около нуля — возможен гололёд, будьте осторожны!")
    if day and day.t_max >= 28:
        tips.append("🥤 Жарко — возьмите с собой воду!")
    if day and day.t_max - day.t_min >= 10:
        tips.append("🧥 Днём и вечером большая разница — оденьтесь слоями!")
    if out and tips:
        return out + [""] + tips
    return out + tips


def header(fc: Forecast) -> str:
    today = fc.day(fc.now.date())
    span = f" · ↑{t(today.t_max)} ↓{t(today.t_min)}" if today else ""
    return f"📍 {fc.place.name}{span}"


def now_block(fc: Forecast) -> str:
    return (f"{icon(fc.code, not fc.is_day)} Сейчас {t(fc.temp)}, {desc(fc.code)}\n"
            f"Ощущается как {t(fc.feels)} · 💨 {round(fc.wind)} м/с")


def city_time(name: str, now: datetime, mine: datetime) -> str:
    diff = round((now.utcoffset() - mine.utcoffset()).total_seconds() / 3600)
    if diff == 0:
        rel = "как у вас"
    else:
        sign = "+" if diff > 0 else "−"
        rel = f"{sign}{abs(diff)} ч от вас"
    return (f"🕐 {name}: {now:%H:%M}\n{WEEKDAYS_FULL[now.weekday()].capitalize()}, "
            f"{now.day} {MONTHS_GEN[now.month - 1]} · {rel}")
