"""🎓 Расписание вуза → «🕰️ Ваш распорядок».

Альфред сам забирает пары с сайта вуза (каждые 30 минут с 7 до 19 и по просьбе «Обнови расписание вуза») и раскладывает их
в распорядок — с напоминаниями, как обычные лекции и практики.

Как сверяет. Для каждой пары Альфред помнит, как она выглядела на сайте в прошлый раз:
- на сайте поменялось (время, аудитория, день) — меняет пару у себя и сообщает, что именно;
- на сайте не менялось — пару не трогает, даже если человек её поправил сам (дописал комментарий и т.п.);
- человек удалил пару сам («лекции не будет») — заново её не добавляет;
- пара пропала с сайта — убирает её и сообщает об отмене (или о переносе, если она появилась в другой день).
Записи человека (тренировки, врач, встречи) не трогаются никогда.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from ..database.repositories import UserRepository
from ..database.schedule_repo import Event
from ..services.tasks import DuplicateError
from ..services.university import UniversityClient, UniversityError
from ..ui import schedule_texts as S
from ..ui import texts as T
from .alfred import Alfred
from .reply import Reply

log = logging.getLogger(__name__)

PREFIX = "uni:"
SYNC_DAYS = 21                 # забираем пары на 3 недели вперёд
SOON_DAYS = 7                  # о новых парах ближе недели — сообщаем; дальше — тихо добавляем
SOURCE_FIELDS = ("type", "title", "date", "start_time", "end_time", "location", "discipline", "pair_no")
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

ERRORS = {
    "wrong_password": "не могу войти на сайт расписания — логин или пароль не подходят. "
                      "Проверьте UNI_LOGIN и UNI_PASSWORD в Railway → Variables",
    "unavailable": "сайт расписания вуза сейчас не отвечает — попробую позже",
}


def lesson_values(item: dict) -> Optional[dict]:
    """Пара с сайта → событие распорядка. None — если запись непонятная (без даты или времени)."""
    day, start = item.get("date"), item.get("start")
    if not day or not start:
        return None
    kind = (item.get("kind") or "").strip()
    low = kind.lower()
    if "лекц" in low:
        etype, title = "lecture", None
    elif any(w in low for w in ("практ", "семинар", "лаборат")):
        etype, title = "practice", None
    else:
        etype, title = "other", kind or "Занятие"           # экзамен, зачёт, консультация…
    teacher = (item.get("teacher") or "").split(",")[0].strip()
    pair = item.get("pairNo")
    notes = [f"Преподаватель: {teacher}" if teacher else "",
             f"Подгруппа {item['subgroup']}" if item.get("subgroup") else ""]
    return {"type": etype, "title": title, "date": day[:10], "start_time": start[:5],
            "end_time": (item.get("end") or "")[:5] or None, "location": item.get("place") or None,
            "discipline": item.get("discipline") or None, "comment": "; ".join(n for n in notes if n) or None,
            "focus": None, "recurrence_id": None, "external_id": PREFIX + str(item.get("id")),
            "pair_no": pair if isinstance(pair, int) and 0 < pair < 20 else None}


def _source(values: dict) -> dict:
    return {k: values.get(k) for k in SOURCE_FIELDS}


@dataclass
class SyncResult:
    first: bool = False
    added: list[dict] = field(default_factory=list)
    changed: list[tuple[dict, dict]] = field(default_factory=list)     # (было, стало)
    removed: list[dict] = field(default_factory=list)
    moved: list[tuple[dict, dict]] = field(default_factory=list)
    upcoming: int = 0
    last_day: Optional[date] = None
    error: Optional[str] = None


def _when(v: dict) -> str:
    d = date.fromisoformat(v["date"])
    return f"{WEEKDAY_SHORT[d.weekday()]} {d.day} {T.MONTHS_GEN[d.month - 1][:3]} {v['start_time']}"


def _name(v: dict) -> str:
    what = v.get("title") or S.TYPE_LABEL.get(v["type"], "Занятие")
    return f"{what} «{T.short(v['discipline'], 45)}»" if v.get("discipline") else what


class UniversitySync:
    def __init__(self, alfred: Alfred, client: UniversityClient, users: UserRepository):
        self.alfred = alfred
        self.client = client
        self.users = users
        self.key = f"uni_known:{alfred.user_id}"

    def _known(self) -> Optional[dict]:
        raw = self.users.setting(self.key)
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    async def run(self) -> SyncResult:
        a = self.alfred
        today, now = a.today(), a.now()
        end = today + timedelta(days=SYNC_DAYS)
        try:
            items = await self.client.lessons(today, end)
        except UniversityError as e:
            return SyncResult(error=str(e))
        lessons = {v["external_id"]: v for v in filter(None, map(lesson_values, items))}
        known = self._known()
        result = SyncResult(first=known is None)
        known = known or {}
        mine = {e.external_id: e for e in a.schedule.between(today - timedelta(days=1), end + timedelta(days=7))
                if e.external_id and e.external_id.startswith(PREFIX)}
        future_known = [k for k, v in known.items() if v["date"] >= today.isoformat()]
        if not lessons and len(future_known) >= 5:
            log.warning("Сайт вуза вернул пустое расписание — ничего не удаляю (возможен сбой сайта)")
            return SyncResult(error="unavailable")
        # «Чистый лист» или восстановление базы стёрли все пары — значит, это не «человек удалил сам»
        wiped = bool(future_known) and not any(k in mine for k in future_known)

        for ext, v in lessons.items():
            ev = mine.get(ext)
            if ext not in known or wiped:
                if ev is None:
                    self._add(v, now)
                    if not wiped:
                        result.added.append(v)
                elif _source(ev.as_values()) != _source(v):
                    a.schedule.update(ev, _source(v), now)
            elif ev is None:
                continue                                   # человек удалил пару сам — не навязываем
            else:
                old, new = known[ext], _source(v)
                # поля, которых Альфред раньше не запоминал (номер пары появился в 8.32), — дописываем молча
                quiet = {k: val for k, val in new.items() if k not in old and ev.as_values().get(k) != val}
                changes = {k: val for k, val in new.items() if k in old and old[k] != val}
                if changes or quiet:
                    a.schedule.update(ev, {**quiet, **changes}, now)
                if changes:
                    result.changed.append((old, new))

        for ext, old in known.items():
            if ext in lessons or old["date"] < today.isoformat():
                continue
            ev = mine.get(ext)
            if ev:
                a.schedule.delete([ev], now)
            result.removed.append(old)

        self._pair_moves(result)
        self.users.set_setting(self.key, json.dumps({k: _source(v) for k, v in lessons.items()}, ensure_ascii=False))
        upcoming = sorted(v["date"] for v in lessons.values() if v["date"] >= today.isoformat())
        result.upcoming = len(upcoming)
        result.last_day = date.fromisoformat(upcoming[-1]) if upcoming else None
        return result

    def _add(self, v: dict, now: datetime) -> None:
        try:
            self.alfred.schedule.create(v, now)
        except DuplicateError:
            # такая пара уже записана человеком вручную — берём её под присмотр, а не дублируем
            same = [e for e in self.alfred.schedule.day(date.fromisoformat(v["date"]))
                    if e.start_time == v["start_time"] and e.type == v["type"] and not e.external_id]
            if same:
                self.alfred.schedule.update(same[0], {**_source(v), "external_id": v["external_id"]}, now)

    @staticmethod
    def _pair_moves(r: SyncResult) -> None:
        """Пара пропала в один день и появилась в другой (тот же предмет и вид) — это перенос, а не отмена."""
        for old in list(r.removed):
            twin = next((n for n in r.added if n.get("discipline") == old.get("discipline")
                         and n["type"] == old["type"]), None)
            if twin:
                r.removed.remove(old)
                r.added.remove(twin)
                r.moved.append((old, _source(twin)))


async def sync_and_report(alfred: Alfred, client: UniversityClient, users: UserRepository,
                          manual: bool = False) -> Optional[Reply]:
    """Сверить расписание и вернуть сообщение для владельца (None — сообщать нечего).
    Об одной и той же ошибке (например, неверный пароль) при плановых проверках сообщаем один раз."""
    r = await UniversitySync(alfred, client, users).run()
    last_error = users.setting("uni_error")
    users.set_setting("uni_error", r.error)
    if r.error and not manual and r.error == last_error:
        return None
    text = sync_text(r, client.group, alfred.today(), manual)
    return Reply(text) if text else None


def sync_text(r: SyncResult, group: str, today: date, manual: bool) -> Optional[str]:
    """Сообщение владельцу. None — сообщать нечего (плановая проверка без изменений)."""
    if r.error:
        if r.error == "unavailable" and not manual:
            return None
        return f"🎩 Сэр, расписание вуза обновить не получилось!\n\n⚠️ {ERRORS.get(r.error, r.error)}"
    till = f" (до {r.last_day.day} {T.MONTHS_GEN[r.last_day.month - 1]})" if r.last_day else ""
    if r.first:
        return (f"🎩 Готово, Сэр! Подключил расписание вуза — группа {group}!\n\n"
                f"📚 Загрузил пар: {r.upcoming}{till}. Они в «🕰️ Ваш распорядок», напоминания включены.\n"
                "🔄 Проверяю сайт каждые 30 минут с 7 до 19 и сразу сообщу, если что-то изменится.")
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    added_soon = [v for v in r.added if v["date"] <= soon]
    lines = []
    for old, new in r.moved:
        lines.append(f"🔄 {_name(new)}: перенесли с {_when(old)} на {_when(new)}"
                     + (f", {new['location']}" if new.get("location") else ""))
    for old, new in r.changed:
        what = []
        if old.get("date") != new.get("date") or old.get("start_time") != new.get("start_time"):
            what.append(f"время {_when(old)} → {_when(new)}")
        if old.get("location") != new.get("location"):
            what.append(f"аудитория {old.get('location') or '—'} → {new.get('location') or '—'}")
        if old.get("end_time") != new.get("end_time") and not what:
            what.append(f"конец в {new.get('end_time')}")
        if old.get("type") != new.get("type") or old.get("title") != new.get("title"):
            what.append(f"теперь это {S.TYPE_NOM.get(new['type'], 'занятие')}")
        lines.append(f"✏️ {_name(new)}, {_when(new)}: " + (", ".join(what) or "изменения в описании"))
    for old in r.removed:
        lines.append(f"❌ Отменена: {_name(old)}, {_when(old)}")
    for v in added_soon:
        lines.append(f"➕ Новая пара: {_name(v)}, {_when(v)}" + (f", {v['location']}" if v.get("location") else ""))
    if lines:
        return "🎩 Сэр, в расписании вуза изменения!\n\n" + "\n".join(lines)
    if manual:
        extra = f"\n➕ Добавил новых пар: {len(r.added)}" if r.added else ""
        return f"🎩 Сэр, расписание вуза актуально!\n\n📚 Пар впереди: {r.upcoming}{till}{extra}"
    return None
