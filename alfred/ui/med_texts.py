"""Тексты медкарты."""

from datetime import date
from typing import Optional

from ..database.med_repo import Case, Contact, Drug
from .birthday_texts import plural
from .texts import MONTHS_GEN

CAUTION = "⚕️ Повторять лечение лучше после консультации с врачом, Сэр."


def d_short(d: date, today: date) -> str:
    s = f"{d.day} {MONTHS_GEN[d.month - 1][:3]}"
    return s if d.year == today.year else f"{s} {d.year}"


def d_long(d: date, today: date) -> str:
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    return s if d.year == today.year else f"{s} {d.year}"


def times_word(n: int) -> str:
    return f"{n} {plural(n, 'раз', 'раза', 'раз')} в день"


def days_word(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def case_button(c: Case, today: date) -> str:
    if c.ended is None:
        return f"🤒 {c.title} · с {d_short(c.started, today)}"[:60]
    return f"✅ {c.title} · {d_short(c.started, today)} – {d_short(c.ended, today)}"[:60]


def drug_block(d: Drug, today: date, closed: bool) -> str:
    parts = [p for p in [d.dose, times_word(d.per_day) if d.per_day else None, d.meal,
                         days_word(d.days) if d.days else None] if p]
    lines = [f"💊 {d.name}", "   " + (" · ".join(parts) if parts else "по назначению врача")]
    if d.stopped or closed or (d.end and d.end < today):
        if d.end and d.end < today:
            lines.append(f"   курс закончен {d_short(d.end, today)}")
        elif d.stopped:
            lines.append("   приём прекращён")
    else:
        when = f"⏰ {', '.join(d.times)}" if d.times else ""
        until = f"до {d_short(d.end, today)} включительно" if d.end else ""
        tail = " · ".join(x for x in (when, until) if x)
        if tail:
            lines.append("   " + tail)
    if d.note:
        lines.append(f"   📝 {d.note}")
    return "\n".join(lines)


def case_card(c: Case, today: date, allergy_note: Optional[str] = None) -> str:
    closed = c.ended is not None
    if closed:
        span = f"📅 {d_long(c.started, today)} — {d_long(c.ended, today)} ({days_word((c.ended - c.started).days + 1)})"
    else:
        span = f"📅 С {d_long(c.started, today)} · болею (день {(today - c.started).days + 1})"
    parts = [f"🎩 {c.title}, Сэр!", span]
    if c.doctor:
        parts[-1] += f"\n👨‍⚕️ Врач: {c.doctor}"
    if c.drugs:
        parts.append("Лечение:\n" + "\n".join(drug_block(d, today, closed) for d in c.drugs))
    if c.notes:
        parts.append(f"📝 {c.notes}")
    if allergy_note:
        parts.append(allergy_note)
    if closed and c.drugs:
        parts.append(CAUTION)
    return "\n\n".join(parts)


def contact_line(c: Contact) -> str:
    head = f"👨‍⚕️ {c.name}" + (f" — {c.specialty}" if c.specialty else "")
    extra = [x for x in (f"📞 {c.phone}" if c.phone else None, f"🏥 {c.place}" if c.place else None) if x]
    return head + ("\n   " + "\n   ".join(extra) if extra else "")


def reminder(d: Drug, c: Case, today: date) -> str:
    what = " · ".join(x for x in (d.dose, d.meal) if x)
    day = ""
    if d.days:
        n = (today - d.start).days + 1
        day = f"\n📅 День {n} из {d.days}"
    return f"🎩 Сэр, пора принять лекарство!\n\n💊 {d.name}" + (f" — {what}" if what else "") + \
        f"\n🤒 {c.title}{day}"
