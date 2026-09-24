"""Хранилище медкарты: случаи болезни, лекарства, приёмы (напоминания), аллергии, врачи."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from .db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(v: Optional[str]) -> Optional[date]:
    return date.fromisoformat(v) if v else None


@dataclass
class Drug:
    id: int
    case_id: int
    name: str
    dose: Optional[str]
    per_day: Optional[int]
    times: list[str]
    meal: Optional[str]
    days: Optional[int]
    start: date
    end: Optional[date]
    stopped: bool
    note: Optional[str]

    def active_on(self, d: date) -> bool:
        return not self.stopped and self.start <= d and (self.end is None or d <= self.end)


@dataclass
class Case:
    id: int
    title: str
    started: date
    ended: Optional[date]
    doctor: Optional[str]
    notes: Optional[str]
    drugs: list[Drug] = field(default_factory=list)


@dataclass
class Allergy:
    id: int
    text: str


@dataclass
class Contact:
    id: int
    name: str
    specialty: Optional[str]
    phone: Optional[str]
    place: Optional[str]


@dataclass
class Dose:
    id: int
    drug_id: int
    due_at: datetime
    status: str


def _drug(r) -> Drug:
    return Drug(r["id"], r["case_id"], r["name"], r["dose"], r["per_day"],
                [t for t in (r["times"] or "").split(",") if t], r["meal"], r["days"],
                _d(r["start_date"]), _d(r["end_date"]), bool(r["stopped"]), r["note"])


class MedRepository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ случаи
    def cases(self, user_id: int) -> list[Case]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM med_cases WHERE user_id=? ORDER BY started DESC, id DESC",
                                (user_id,)).fetchall()
            drugs = conn.execute("SELECT * FROM med_drugs WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        by_case: dict[int, list[Drug]] = {}
        for r in drugs:
            by_case.setdefault(r["case_id"], []).append(_drug(r))
        return [Case(r["id"], r["title"], _d(r["started"]), _d(r["ended"]), r["doctor"], r["notes"],
                     by_case.get(r["id"], [])) for r in rows]

    def case(self, user_id: int, case_id: int) -> Optional[Case]:
        return next((c for c in self.cases(user_id) if c.id == case_id), None)

    def add_case(self, user_id: int, title: str, started: date, doctor: Optional[str], notes: Optional[str]) -> int:
        with self.db.connect() as conn:
            return conn.execute(
                "INSERT INTO med_cases (user_id, title, started, doctor, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)", (user_id, title, started.isoformat(), doctor, notes, _now(), _now())
            ).lastrowid

    def update_case(self, user_id: int, case_id: int, **values) -> None:
        allowed = {k: v for k, v in values.items() if k in ("title", "ended", "doctor", "notes", "started")}
        with self.db.connect() as conn:
            for k, v in allowed.items():
                v = v.isoformat() if isinstance(v, date) else v
                conn.execute(f"UPDATE med_cases SET {k}=?, updated_at=? WHERE id=? AND user_id=?",
                             (v, _now(), case_id, user_id))

    def delete_case(self, user_id: int, case_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM med_doses WHERE drug_id IN (SELECT id FROM med_drugs WHERE case_id=?)",
                         (case_id,))
            conn.execute("DELETE FROM med_drugs WHERE case_id=? AND user_id=?", (case_id, user_id))
            conn.execute("DELETE FROM med_cases WHERE id=? AND user_id=?", (case_id, user_id))

    # ------------------------------------------------------------ лекарства
    def drug(self, user_id: int, drug_id: int) -> Optional[Drug]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM med_drugs WHERE id=? AND user_id=?", (drug_id, user_id)).fetchone()
        return _drug(r) if r else None

    def add_drug(self, user_id: int, case_id: int, d: dict) -> int:
        with self.db.connect() as conn:
            return conn.execute(
                "INSERT INTO med_drugs (user_id, case_id, name, dose, per_day, times, meal, days, start_date, "
                "end_date, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, case_id, d["name"], d.get("dose"), d.get("per_day"), ",".join(d.get("times") or []),
                 d.get("meal"), d.get("days"), d["start"].isoformat(),
                 d["end"].isoformat() if d.get("end") else None, d.get("note"), _now(), _now())
            ).lastrowid

    def update_drug(self, user_id: int, drug_id: int, **values) -> None:
        cols = {"dose", "per_day", "times", "meal", "days", "end_date", "stopped", "note", "name"}
        with self.db.connect() as conn:
            for k, v in values.items():
                if k not in cols:
                    continue
                if k == "times":
                    v = ",".join(v or [])
                v = v.isoformat() if isinstance(v, date) else v
                conn.execute(f"UPDATE med_drugs SET {k}=?, updated_at=? WHERE id=? AND user_id=?",
                             (v, _now(), drug_id, user_id))

    # ------------------------------------------------------------ приёмы
    def dose_exists(self, user_id: int, drug_id: int, due_at: datetime) -> bool:
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM med_doses WHERE user_id=? AND drug_id=? AND due_at=?",
                                (user_id, drug_id, due_at.isoformat())).fetchone() is not None

    def add_dose(self, user_id: int, drug_id: int, due_at: datetime, status: str) -> int:
        with self.db.connect() as conn:
            return conn.execute("INSERT INTO med_doses (user_id, drug_id, due_at, status, created_at) "
                                "VALUES (?,?,?,?,?)", (user_id, drug_id, due_at.isoformat(), status, _now())
                                ).lastrowid

    def dose(self, user_id: int, dose_id: int) -> Optional[Dose]:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM med_doses WHERE id=? AND user_id=?", (dose_id, user_id)).fetchone()
        return Dose(r["id"], r["drug_id"], datetime.fromisoformat(r["due_at"]), r["status"]) if r else None

    def pending_doses(self, user_id: int, until: datetime) -> list[Dose]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM med_doses WHERE user_id=? AND status='pending' AND due_at<=?",
                                (user_id, until.isoformat())).fetchall()
        return [Dose(r["id"], r["drug_id"], datetime.fromisoformat(r["due_at"]), r["status"]) for r in rows]

    def set_dose(self, user_id: int, dose_id: int, status: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE med_doses SET status=? WHERE id=? AND user_id=?", (status, dose_id, user_id))

    # ------------------------------------------------------------ аллергии и врачи
    def allergies(self, user_id: int) -> list[Allergy]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id, text FROM med_allergies WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        return [Allergy(r["id"], r["text"]) for r in rows]

    def add_allergy(self, user_id: int, text: str) -> None:
        with self.db.connect() as conn:
            conn.execute("INSERT INTO med_allergies (user_id, text, created_at) VALUES (?,?,?)", (user_id, text, _now()))

    def delete_allergy(self, user_id: int, allergy_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM med_allergies WHERE id=? AND user_id=?", (allergy_id, user_id))

    def contacts(self, user_id: int) -> list[Contact]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM med_contacts WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        return [Contact(r["id"], r["name"], r["specialty"], r["phone"], r["place"]) for r in rows]

    def add_contact(self, user_id: int, c: dict) -> int:
        with self.db.connect() as conn:
            return conn.execute("INSERT INTO med_contacts (user_id, name, specialty, phone, place, created_at) "
                                "VALUES (?,?,?,?,?,?)", (user_id, c["name"], c.get("specialty"), c.get("phone"),
                                                        c.get("place"), _now())).lastrowid

    def update_contact(self, user_id: int, contact_id: int, c: dict) -> None:
        with self.db.connect() as conn:
            for k in ("specialty", "phone", "place"):
                if c.get(k):
                    conn.execute(f"UPDATE med_contacts SET {k}=? WHERE id=? AND user_id=?", (c[k], contact_id, user_id))

    def delete_contact(self, user_id: int, contact_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM med_contacts WHERE id=? AND user_id=?", (contact_id, user_id))
