"""Медкарта: разбор назначений (сколько раз в день, до/после еды, сколько дней), напоминания о приёме."""

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from ..database.med_repo import Allergy, Case, Contact, Dose, Drug, MedRepository
from . import search
from .tasks import VerificationError

DEFAULT_TIMES = {1: ["09:00"], 2: ["09:00", "21:00"], 3: ["08:00", "14:00", "20:00"],
                 4: ["08:00", "12:00", "16:00", "20:00"], 5: ["08:00", "11:00", "14:00", "17:00", "20:00"],
                 6: ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]}
WORD_NUM = {"один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6}
GRACE = timedelta(minutes=15)   # если бот был выключен — напоминаем, только если опоздали не больше чем на 15 мин
SNOOZE = timedelta(minutes=15)


def _n(s: str) -> Optional[int]:
    s = s.lower()
    return int(s) if s.isdigit() else WORD_NUM.get(s)


def parse_per_day(text: str) -> Optional[int]:
    t = text.lower().replace("ё", "е")
    m = re.search(r"\b(\d|один|два|две|три|четыре|пять|шесть)\s*(?:раз\w*|р)\s*(?:в|/)\s*(?:день|сутки|д)\b", t)
    if m:
        return _n(m.group(1))
    if re.search(r"утром и вечером|утро и вечер|утром, вечером", t):
        return 2
    if re.search(r"\bраз в день\b|\bодин раз\b|\bежедневно\b|на ночь|перед сном", t):
        return 1
    return None


def parse_times(values, text: str = "") -> list[str]:
    """«в 8 и в 20», ["08:00","20:00"], «утром и вечером», «на ночь» → список «ЧЧ:ММ»."""
    found = []
    for v in (values or []):
        m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$", str(v))
        if m and int(m.group(1)) < 24:
            found.append(f"{int(m.group(1)):02d}:{m.group(2) or '00'}")
    if not found and text:
        t = text.lower()
        for m in re.finditer(r"\bв\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*(?:раз|дн|день|таб|кап|мг|мл))", t):
            if int(m.group(1)) < 24:
                found.append(f"{int(m.group(1)):02d}:{m.group(2) or '00'}")
        if not found:
            if re.search(r"утром и вечером", t):
                found = ["09:00", "21:00"]
            elif re.search(r"на ночь|перед сном", t):
                found = ["22:00"]
    return sorted(dict.fromkeys(found))


def parse_days(text: str) -> Optional[int]:
    t = text.lower().replace("ё", "е")
    m = re.search(r"\b(\d{1,3})\s*(?:дн|день|дня|дней|сут)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d|одну|две|три|четыре)?\s*недел", t)
    if m:
        return 7 * (_n(m.group(1)) if m.group(1) else 1)
    if re.search(r"\bмесяц", t):
        return 30
    return None


def parse_meal(text: Optional[str]) -> Optional[str]:
    t = (text or "").lower()
    if "натощак" in t:
        return "натощак"
    if re.search(r"до\s+еды|перед\s+едой|до\s+приема\s+пищи", t):
        return "до еды"
    if re.search(r"после\s+еды|после\s+приема\s+пищи", t):
        return "после еды"
    if re.search(r"во\s+время\s+еды|во\s+время\s+приема\s+пищи|с\s+едой", t):
        return "во время еды"
    return None


def parse_dose(text: str) -> Optional[str]:
    m = re.search(r"\b(\d+[.,]?\d*|одн\w*|две|два|три|пол|половин\w*)\s*"
                  r"(таблет\w*|капсул\w*|капел\w*|капл\w*|мг|мл|пакет\w*|пакетик\w*|ложк\w*|впрыск\w*|доз\w*|пшик\w*)",
                  text.lower())
    return f"{m.group(1)} {m.group(2)}" if m else None


def _cap(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip().rstrip(".")
    return s[:1].upper() + s[1:] if s else None


def build_drug(raw: dict, backup_text: str, start: date) -> Optional[dict]:
    """Приводит лекарство от ИИ в порядок. backup_text — слова пользователя, если лекарство одно."""
    name = _cap(raw.get("name"))
    if not name:
        return None
    text = backup_text or ""
    try:
        per_day = int(raw.get("per_day")) if raw.get("per_day") else None
    except (TypeError, ValueError):
        per_day = None
    per_day = per_day or parse_per_day(text)
    times = parse_times(raw.get("times"), text)
    if times and not per_day:
        per_day = len(times)
    if per_day and (not times or len(times) != per_day):
        times = DEFAULT_TIMES.get(per_day, times)
    try:
        days = int(raw.get("days")) if raw.get("days") else None
    except (TypeError, ValueError):
        days = None
    days = days or parse_days(text)
    return {"name": name, "dose": (raw.get("dose") or parse_dose(text) or None),
            "per_day": per_day, "times": times,
            "meal": parse_meal(raw.get("meal")) or parse_meal(text), "days": days,
            "start": start, "end": start + timedelta(days=days - 1) if days else None,
            "note": _cap(raw.get("note"))}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яa-z]+", search.normalize(text)) if len(w) >= 4}


class MedService:
    def __init__(self, repo: MedRepository, user_id: int):
        self.repo = repo
        self.user_id = user_id

    # ------------------------------------------------------------ случаи
    def cases(self) -> list[Case]:
        return self.repo.cases(self.user_id)

    def case(self, case_id: int) -> Optional[Case]:
        return self.repo.case(self.user_id, case_id)

    def open_cases(self) -> list[Case]:
        return [c for c in self.cases() if c.ended is None]

    def find(self, query: str) -> list[Case]:
        scored = [(search.score(query, f"{c.title} {c.notes or ''} " + " ".join(d.name for d in c.drugs)), c)
                  for c in self.cases()]
        best = max((s for s, _ in scored), default=0)
        return [c for s, c in scored if s == best] if best else []

    def add_case(self, title: str, started: date, doctor: Optional[str], notes: Optional[str],
                 drugs: list[dict]) -> Case:
        cid = self.repo.add_case(self.user_id, _cap(title), started, _cap(doctor), _cap(notes))
        for d in drugs:
            self.repo.add_drug(self.user_id, cid, d)
        case = self.case(cid)
        if not case or len(case.drugs) != len(drugs):
            raise VerificationError("case not saved")
        return case

    def add_drugs(self, case: Case, drugs: list[dict]) -> Case:
        for d in drugs:
            self.repo.add_drug(self.user_id, case.id, d)
        return self.case(case.id)

    def update_case(self, case: Case, **values) -> Case:
        self.repo.update_case(self.user_id, case.id, **values)
        return self.case(case.id)

    def recover(self, case: Case, day: date) -> Case:
        """Выздоровел: случай закрыт, недопитые лекарства больше не напоминаем."""
        self.repo.update_case(self.user_id, case.id, ended=day)
        for d in case.drugs:
            if d.active_on(day) or (d.end is None and not d.stopped):
                self.repo.update_drug(self.user_id, d.id, stopped=1)
        return self.case(case.id)

    def delete_case(self, case: Case) -> None:
        self.repo.delete_case(self.user_id, case.id)
        if self.case(case.id):
            raise VerificationError("case not deleted")

    # ------------------------------------------------------------ лекарства
    def drug(self, drug_id: int) -> Optional[Drug]:
        return self.repo.drug(self.user_id, drug_id)

    def find_drug(self, name: str, only_active: bool = True, today: Optional[date] = None) -> list[Drug]:
        drugs = [d for c in self.cases() for d in c.drugs
                 if not only_active or (not d.stopped and (d.end is None or (today and d.end >= today)))]
        scored = [(search.score(name, d.name), d) for d in drugs]
        best = max((s for s, _ in scored), default=0)
        return [d for s, d in scored if s == best] if best else []

    def update_drug(self, drug: Drug, changes: dict) -> Drug:
        values = {}
        if changes.get("dose"):
            values["dose"] = changes["dose"]
        if changes.get("meal"):
            values["meal"] = changes["meal"]
        if changes.get("per_day") or changes.get("times"):
            values["per_day"] = changes.get("per_day") or len(changes["times"])
            values["times"] = changes.get("times") or DEFAULT_TIMES.get(values["per_day"], drug.times)
        if changes.get("days"):
            values["days"] = changes["days"]
            values["end_date"] = drug.start + timedelta(days=changes["days"] - 1)
        if changes.get("note"):
            values["note"] = changes["note"]
        self.repo.update_drug(self.user_id, drug.id, **values)
        return self.drug(drug.id)

    def stop_drug(self, drug: Drug) -> None:
        self.repo.update_drug(self.user_id, drug.id, stopped=1)

    def allergy_hits(self, drug_name: str) -> list[str]:
        stems = _stems(drug_name)
        return [a.text for a in self.allergies() if stems & _stems(a.text)]

    # ------------------------------------------------------------ напоминания
    def due(self, now: datetime) -> list[tuple[Dose, Drug, Case]]:
        """Кому пора напомнить прямо сейчас. Каждый приём — один раз."""
        out = []
        today = now.date()
        for case in self.open_cases():
            for d in case.drugs:
                if not d.active_on(today):
                    continue
                for t in d.times:
                    hh, mm = map(int, t.split(":"))
                    due_at = datetime.combine(today, time(hh, mm), tzinfo=now.tzinfo)
                    if due_at <= now < due_at + GRACE and not self.repo.dose_exists(self.user_id, d.id, due_at):
                        dose_id = self.repo.add_dose(self.user_id, d.id, due_at, "sent")
                        out.append((self.repo.dose(self.user_id, dose_id), d, case))
        for dose in self.repo.pending_doses(self.user_id, now):
            drug = self.drug(dose.drug_id)
            case = self.case(drug.case_id) if drug else None
            self.repo.set_dose(self.user_id, dose.id, "sent")
            if drug and case and case.ended is None and not drug.stopped:
                out.append((dose, drug, case))
        return out

    def dose(self, dose_id: int) -> Optional[Dose]:
        return self.repo.dose(self.user_id, dose_id)

    def took(self, dose: Dose) -> None:
        self.repo.set_dose(self.user_id, dose.id, "taken")

    def snooze(self, dose: Dose, now: datetime) -> datetime:
        self.repo.set_dose(self.user_id, dose.id, "snoozed")
        at = now + SNOOZE
        self.repo.add_dose(self.user_id, dose.drug_id, at, "pending")
        return at

    # ------------------------------------------------------------ аллергии и врачи
    def allergies(self) -> list[Allergy]:
        return self.repo.allergies(self.user_id)

    def add_allergy(self, text: str) -> bool:
        text = _cap(text)
        if any(search.normalize(a.text) == search.normalize(text) for a in self.allergies()):
            return False
        self.repo.add_allergy(self.user_id, text)
        return True

    def remove_allergy(self, text: str) -> list[str]:
        hits = [a for a in self.allergies() if _stems(a.text) & _stems(text)]
        for a in hits:
            self.repo.delete_allergy(self.user_id, a.id)
        return [a.text for a in hits]

    def delete_allergy(self, allergy_id: int) -> None:
        self.repo.delete_allergy(self.user_id, allergy_id)

    def contacts(self) -> list[Contact]:
        return self.repo.contacts(self.user_id)

    def save_contact(self, c: dict) -> tuple[Contact, bool]:
        """Новый врач или дополнение к уже записанному (по имени). Возвращает (врач, новый ли)."""
        same = [x for x in self.contacts() if search.normalize(x.name) == search.normalize(c["name"])]
        if same:
            self.repo.update_contact(self.user_id, same[0].id, c)
            return next(x for x in self.contacts() if x.id == same[0].id), False
        cid = self.repo.add_contact(self.user_id, c)
        return next(x for x in self.contacts() if x.id == cid), True

    def find_contacts(self, query: str) -> list[Contact]:
        scored = [(search.score(query, f"{x.name} {x.specialty or ''} {x.place or ''}"), x) for x in self.contacts()]
        best = max((s for s, _ in scored), default=0)
        return [x for s, x in scored if s == best] if best else []

    def delete_contact(self, contact_id: int) -> None:
        self.repo.delete_contact(self.user_id, contact_id)
