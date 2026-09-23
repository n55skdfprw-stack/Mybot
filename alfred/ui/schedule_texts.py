"""Оформление распорядка. Строки с данными — без «!», предложения — с «!»."""

from datetime import date

from ..database.schedule_repo import Event
from .texts import MONTHS_GEN

TYPE_LABEL = {"lecture": "Лекция", "practice": "Практика", "training": "Тренировка",
              "doctor": "Врач", "meeting": "Встреча", "other": "Событие"}
TYPE_NOM = {"lecture": "лекция", "practice": "практика", "training": "тренировка",
            "doctor": "приём у врача", "meeting": "встреча", "other": "событие"}
TYPE_ACC = {"lecture": "лекцию", "practice": "практику", "training": "тренировку",
            "doctor": "приём у врача", "meeting": "встречу", "other": "событие"}
TYPE_PLURAL = {"lecture": "лекции", "practice": "практики", "training": "тренировки",
               "doctor": "приёмы у врача", "meeting": "встречи", "other": "события"}

WEEKDAY_CAP = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAY_DAT_PL = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
CLOCKS = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]


def day_title(d: date, today: date) -> str:
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    delta = (d - today).days
    if delta == 0:
        return f"Сегодня, {base}"
    if delta == 1:
        return f"Завтра, {base}"
    return f"{WEEKDAY_CAP[d.weekday()]}, {base}"


def label(e: Event) -> str:
    name = e.title or TYPE_LABEL.get(e.type, "Событие")
    if e.type == "training" and e.focus:
        name += f" ({e.focus})"
    return name


def time_span(e: Event) -> str:
    return f"{e.start_time}–{e.end_time}" if e.end_time else e.start_time


def details(e: Event) -> list[str]:
    lines = []
    if e.discipline:
        lines.append(f"📚 {e.discipline}")
    if e.location:
        lines.append(f"🚪 {e.location}")
    if e.comment:
        lines.append(f"💬 {e.comment}")
    return lines


def block(e: Event, clock: bool = False) -> str:
    icon = CLOCKS[int(e.start_time[:2]) % 12] + " " if clock else ""
    return "\n".join([f"{icon}{time_span(e)} — {label(e)}", *details(e)])


def repeat_description(kind: str, weekdays: list[int], month_day: int | None) -> str:
    if kind == "monthly":
        return f"Каждый месяц, {month_day}-го числа"
    days = sorted(weekdays)
    if days == [0, 1, 2, 3, 4]:
        return "По будням"
    if days == [5, 6]:
        return "По выходным"
    if days == list(range(7)):
        return "Каждый день"
    names = [WEEKDAY_DAT_PL[d] for d in days]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " и " + names[-1]
    return "По " + joined


def short_choice(e: Event, today: date) -> str:
    d = day_title(e.date, today).split(",")[0]
    return f"{d}, {e.date.day:02d}.{e.date.month:02d} — {e.start_time} {label(e)}"
