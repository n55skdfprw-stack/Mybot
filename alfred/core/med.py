"""Раздел «🩺 Медкарта»: болезни и чем лечился, лекарства с напоминаниями, аллергии, врачи."""

import re
from dataclasses import replace
from typing import Optional

from ..brain.dates import find_dates
from ..brain.parser import BrainResult
from ..database.med_repo import Case
from ..services import search
from ..services.dossier import find_phone
from ..services.med import MedService, build_drug
from ..ui import med_texts as M
from .reply import Reply

MED_INTENTS = {"MED_CASE", "MED_DRUG", "MED_RECOVER", "MED_SHOW", "MED_DELETE", "MED_ALLERGY", "MED_CONTACT"}
RECOVER_RE = re.compile(r"\b(выздоровел\w*|поправил\w*|вылечил\w*)\b")
ALLERGY_RE = re.compile(r"(?:аллерги\w*|непереносимост\w*)\s+(?:на\s+|к\s+)?(.+)", re.IGNORECASE)
ALLERGY_OFF_RE = re.compile(r"больше нет|уже нет|прошла|убери|удали|нет аллерги", re.IGNORECASE)


def allergy_from(text: str) -> Optional[str]:
    """«У меня аллергия на пенициллин» → «пенициллин» (если ИИ не вытащил сам)."""
    m = ALLERGY_RE.search(text or "")
    if not m:
        return None
    what = re.split(r"\s+(?:больше|уже)\s+нет|\s+прошла", m.group(1), maxsplit=1)[0]
    what = what.strip(" .!,")
    if what and m.group(0).lower().startswith("непереносим"):
        return f"непереносимость {what}"          # «лактозы» само по себе читается странно
    return what or None


class MedMixin:
    med: MedService

    # ------------------------------------------------------------ помощники
    def _m(self, r: BrainResult) -> dict:
        return r.med or {}

    def _illness(self, r: BrainResult) -> Optional[str]:
        return r.illness or self._m(r).get("illness")

    def _start_day(self):
        found = [d for d in find_dates(self._raw_text or self._message or "", self.today()) if d <= self.today()]
        return found[0] if found else self.today()

    def _build_drugs(self, r: BrainResult, start) -> list[dict]:
        raw = [d for d in (self._m(r).get("drugs") or []) if isinstance(d, dict)]
        backup = (self._message or self._raw_text or "") if len(raw) == 1 else ""
        return [x for x in (build_drug(d, backup, start) for d in raw) if x]

    def _last_case(self) -> Optional[Case]:
        ctx = self._ctx()
        if ctx.entity_type == "med_case" and ctx.entity_id:
            return self.med.case(ctx.entity_id)
        return None

    def _ambiguous_cases(self, r: BrainResult, cases: list[Case]) -> Reply:
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка",
                                           "choose": "med_case"})
        rows = [[(M.case_button(c, self.today()), f"pick:{c.id}")] for c in cases[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply("🎩 Сэр, уточните, пожалуйста, о какой болезни речь?", buttons=rows)

    def _pick_case(self, r: BrainResult, open_only: bool = True) -> Case | Reply | None:
        """Болезнь по названию, иначе — последняя, о которой говорили, иначе — единственная текущая."""
        illness = self._illness(r)
        pool = self.med.open_cases() if open_only else self.med.cases()
        if illness:
            found = [c for c in self.med.find(illness) if c in pool] or \
                    ([] if open_only else self.med.find(illness))
            if len(found) > 1:
                return self._ambiguous_cases(r, found)
            return found[0] if found else None
        last = self._last_case()
        if last and (not open_only or last.ended is None):
            return last
        if len(pool) == 1:
            return pool[0]
        return self._ambiguous_cases(r, pool) if pool else None

    def _allergy_warning(self, drugs: list[dict]) -> Optional[str]:
        hits = sorted({a for d in drugs for a in self.med.allergy_hits(d["name"])})
        return f"⚠️ Внимание, Сэр: у вас записана аллергия — {', '.join(hits)}!" if hits else None

    # ------------------------------------------------------------ защита от ошибок ИИ
    def _guard_med(self, r: BrainResult, text: str) -> BrainResult:
        t = search.normalize(text)
        if r.intent not in MED_INTENTS and RECOVER_RE.search(t) and len(t.split()) <= 6:
            return replace(r, intent="MED_RECOVER")
        # «У меня аллергия на пенициллин» — ИИ мог выбрать не то или не заполнить, на что аллергия.
        what = allergy_from(text)
        if what and r.intent not in ("MED_CASE", "MED_DRUG"):
            med = dict(r.med or {})
            med.setdefault("allergy", what)
            if not med.get("allergy"):
                med["allergy"] = what
            if ALLERGY_OFF_RE.search(t):
                med["remove"] = True
            return replace(r, intent="MED_ALLERGY", med=med)
        return r

    # ------------------------------------------------------------ действия
    def _med_case(self, r: BrainResult, chosen: Optional[Case] = None) -> Reply:
        illness = self._illness(r)
        if not illness:
            return self._ask(r, "illness", "🎩 Сочувствую, Сэр! Чем заболели?")
        start = self._start_day()
        drugs = self._build_drugs(r, start)
        same = [c for c in self.med.open_cases() if search.normalize(c.title) == search.normalize(illness)]
        m = self._m(r)
        if same:
            case = self.med.add_drugs(same[0], drugs)
            if m.get("doctor") or m.get("notes"):
                case = self.med.update_case(case, doctor=m.get("doctor") or case.doctor,
                                            notes=m.get("notes") or case.notes)
            head = "🎩 Дополнил медкарту, Сэр!"
        else:
            case = self.med.add_case(illness, start, m.get("doctor"), m.get("notes"), drugs)
            head = "🎩 Записал в медкарту, Сэр! Поправляйтесь!"
        self._set_last("med_case", case.id)
        return self._case_saved(case, head, drugs)

    def _case_saved(self, case: Case, head: str, drugs: list[dict]) -> Reply:
        text = M.case_card(case, self.today()).split("\n\n", 1)[1]
        extra = []
        if any(d.get("times") for d in drugs):
            extra.append("⏰ Буду напоминать о приёме, Сэр.")
        warning = self._allergy_warning(drugs)
        if warning:
            extra.append(warning)
        body = f"🤒 {case.title}\n{text}" + ("\n\n" + "\n".join(extra) if extra else "")
        return Reply(f"{head}\n\n{body}")

    def _med_drug(self, r: BrainResult, chosen: Optional[Case] = None) -> Reply:
        m = self._m(r)
        action = m.get("action") or ("stop" if m.get("remove") else "add")
        if action in ("change", "stop"):
            name = m.get("drug") or next((d.get("name") for d in m.get("drugs") or [] if isinstance(d, dict)), None)
            if not name:
                return Reply("🎩 Сэр, о каком лекарстве речь?")
            found = self.med.find_drug(name, today=self.today())
            if not found:
                return Reply(f"🎩 Сэр, в текущем лечении нет лекарства «{name}»!")
            drug = found[-1]
            if action == "stop":
                self.med.stop_drug(drug)
                return Reply(f"🎩 Как скажете, Сэр! Больше не напоминаю про {drug.name}.")
            raw = next((d for d in m.get("drugs") or [] if isinstance(d, dict)), {})
            changes = build_drug({**raw, "name": drug.name}, self._message or "", drug.start) or {}
            updated = self.med.update_drug(drug, changes)
            case = self.med.case(updated.case_id)
            self._set_last("med_case", case.id)
            return Reply("🎩 Готово, Сэр! Исправил назначение!\n\n" + M.drug_block(updated, self.today(), False))
        drugs_raw = [d for d in m.get("drugs") or [] if isinstance(d, dict)]
        if not drugs_raw:
            return Reply("🎩 Сэр, какое лекарство записать?")
        case = chosen or self._pick_case(r)
        if isinstance(case, Reply):
            return case
        if case is None:
            if not self._illness(r):
                return self._ask(r, "illness", "🎩 Разумеется, Сэр! От чего это лечение?")
            return self._med_case(r)
        drugs = self._build_drugs(r, self.today())
        case = self.med.add_drugs(case, drugs)
        self._set_last("med_case", case.id)
        return self._case_saved(case, "🎩 Добавил в лечение, Сэр!", drugs)

    def _med_recover(self, r: BrainResult, chosen: Optional[Case] = None) -> Reply:
        case = chosen or self._pick_case(r)
        if isinstance(case, Reply):
            return case
        if case is None:
            return Reply("🎩 Сэр, в медкарте нет текущих болезней!")
        had_reminders = any(d.times and d.active_on(self.today()) for d in case.drugs)
        case = self.med.recover(case, self.today())
        self._set_last("med_case", case.id)
        tail = "\nНапоминания о лекарствах остановлены." if had_reminders else ""
        return Reply(f"🎩 Рад слышать, Сэр! Выздоровление записано!\n\n{M.case_button(case, self.today())}{tail}")

    def _med_show(self, r: BrainResult, chosen: Optional[Case] = None) -> Reply:
        if chosen:
            return self.med_case_view(chosen.id, edit=True)
        m = self._m(r)
        query = m.get("query") or self._illness(r) or m.get("drug")
        if not query:
            return self.medcard_view()
        found = self.med.find(query)
        if not found:
            return Reply(f"🎩 Сэр, в медкарте про «{query}» ничего нет!")
        if len(found) == 1:
            return self.med_case_view(found[0].id, edit=False)
        rows = [[(M.case_button(c, self.today()), f"med:case:{c.id}")] for c in found[:10]]
        return Reply("🎩 Нашёл несколько случаев, Сэр!", buttons=rows)

    def _med_delete(self, r: BrainResult, chosen: Optional[Case] = None) -> Reply:
        case = chosen or self._pick_case(r, open_only=False)
        if isinstance(case, Reply):
            return case
        if case is None:
            return Reply("🎩 Сэр, в медкарте такого нет!")
        return self._confirm_case_delete(case, edit=bool(chosen))

    def _med_allergy(self, r: BrainResult) -> Reply:
        m = self._m(r)
        text = m.get("allergy")
        if not text:
            return self.allergies_view(edit=False)
        if m.get("remove"):
            removed = self.med.remove_allergy(text)
            if not removed:
                return Reply(f"🎩 Сэр, аллергии на «{text}» в медкарте нет!")
            return Reply(f"🎩 Готово, Сэр! Убрал из аллергий: {', '.join(removed)}")
        self.med.add_allergy(text)
        items = ", ".join(a.text for a in self.med.allergies())
        return Reply(f"🎩 Записал, Сэр! Буду иметь в виду.\n\n⚠️ Аллергии: {items}")

    def _med_contact(self, r: BrainResult) -> Reply:
        c = dict(self._m(r).get("contact") or {})
        name = (c.get("name") or "").strip()
        if not name:
            return self.contacts_view(edit=False)
        if self._m(r).get("remove"):
            found = self.med.find_contacts(name)
            if not found:
                return Reply(f"🎩 Сэр, врача «{name}» в медкарте нет!")
            for x in found:
                self.med.delete_contact(x.id)
            return Reply(f"🎩 Удалил, Сэр!\n\n❌ {found[0].name}")
        phone = find_phone(self._raw_text or "")
        if phone:
            c["phone"] = phone
        if not (c.get("phone") or c.get("place") or c.get("specialty")):
            found = self.med.find_contacts(name)
            if found:
                return Reply("🎩 Разумеется, Сэр!\n\n" + "\n\n".join(M.contact_line(x) for x in found[:5]))
        c["name"] = name[:1].upper() + name[1:]
        contact, new = self.med.save_contact(c)
        head = "🎩 Записал врача, Сэр!" if new else "🎩 Дополнил, Сэр!"
        return Reply(f"{head}\n\n{M.contact_line(contact)}")

    # ------------------------------------------------------------ экраны
    def _med_buttons(self) -> list:
        return [[("⚠️ Аллергии", "med:all"), ("👨‍⚕️ Врачи", "med:docs")]]

    def medcard_view(self, edit: bool = False) -> Reply:
        today = self.today()
        cases = self.med.cases()
        allergies = self.med.allergies()
        if not cases:
            return Reply("🎩 Сэр, медкарта пока пуста!\n\nНапример: «Заболел ангиной, врач назначил "
                         "амоксициллин 500 мг 3 раза в день после еды 7 дней».",
                         buttons=self._med_buttons(), edit=edit)
        now = [c for c in cases if c.ended is None]
        text = "🎩 Медкарта, Сэр!"
        if allergies:
            text += "\n\n⚠️ Аллергии: " + ", ".join(a.text for a in allergies)
        if not now:
            text += "\n\n💪 Сейчас вы здоровы!"
        rows = [[(M.case_button(c, today), f"med:case:{c.id}")] for c in (now + [c for c in cases if c.ended])[:12]]
        return Reply(text, buttons=rows + self._med_buttons(), edit=edit)

    def med_case_view(self, case_id: int, edit: bool = True) -> Reply:
        case = self.med.case(case_id)
        if not case:
            return self.medcard_view(edit=edit)
        self._set_last("med_case", case.id)
        rows = []
        if case.ended is None:
            rows.append([("✅ Я выздоровел", f"med:rec:{case.id}")])
        rows.append([("❌ Удалить", f"med:del:{case.id}"), ("↩️ Назад", "med:list")])
        return Reply(M.case_card(case, self.today()), buttons=rows, edit=edit)

    def _confirm_case_delete(self, case: Case, edit: bool = True) -> Reply:
        rows = [[("✅ Да, удалить", f"med:delyes:{case.id}")], [("↩️ Нет", f"med:case:{case.id}")]]
        return Reply(f"🎩 Сэр, удалить из медкарты: {case.title}?", buttons=rows, edit=edit)

    def allergies_view(self, edit: bool = True) -> Reply:
        items = self.med.allergies()
        back = [[("↩️ Назад", "med:list")]]
        if not items:
            return Reply("🎩 Сэр, аллергии пока не записаны!\n\nНапример: «У меня аллергия на пенициллин».",
                         buttons=back if edit else None, edit=edit)
        rows = [[(f"❌ {a.text}"[:60], f"med:alldel:{a.id}")] for a in items]
        return Reply("🎩 Аллергии и непереносимость, Сэр!\n\n" + "\n".join(f"⚠️ {a.text}" for a in items),
                     buttons=rows + back, edit=edit)

    def contacts_view(self, edit: bool = True) -> Reply:
        items = self.med.contacts()
        back = [[("↩️ Назад", "med:list")]]
        if not items:
            return Reply("🎩 Сэр, врачи пока не записаны!\n\nНапример: «Мой терапевт — Иванова Анна Петровна, "
                         "поликлиника №5, телефон +7 812 123-45-67».", buttons=back if edit else None, edit=edit)
        rows = [[(f"❌ {c.name}"[:60], f"med:docdel:{c.id}")] for c in items]
        return Reply("🎩 Ваши врачи и клиники, Сэр!\n\n" + "\n\n".join(M.contact_line(c) for c in items),
                     buttons=rows + back, edit=edit)

    # ------------------------------------------------------------ кнопки
    def med_callback(self, parts: list[str]) -> Reply:
        action = parts[1]
        num = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        if action == "list":
            return self.medcard_view(edit=True)
        if action == "case" and num:
            return self.med_case_view(num)
        if action == "rec" and num:
            case = self.med.case(num)
            if case and case.ended is None:
                self.med.recover(case, self.today())
            return self.med_case_view(num)
        if action == "del" and num:
            case = self.med.case(num)
            return self._confirm_case_delete(case) if case else self.medcard_view(edit=True)
        if action == "delyes" and num:
            case = self.med.case(num)
            if case:
                self.med.delete_case(case)
                self._set_last("med_case", None)
                return Reply(f"🎩 Удалил из медкарты, Сэр!\n\n❌ {case.title}", edit=True)
            return self.medcard_view(edit=True)
        if action == "all":
            return self.allergies_view()
        if action == "alldel" and num:
            self.med.delete_allergy(num)
            return self.allergies_view()
        if action == "docs":
            return self.contacts_view()
        if action == "docdel" and num:
            self.med.delete_contact(num)
            return self.contacts_view()
        if action in ("took", "snooze") and num:
            dose = self.med.dose(num)
            drug = self.med.drug(dose.drug_id) if dose else None
            if not dose or not drug:
                return Reply("🎩 Сэр, это напоминание уже неактуально!", clear_source_buttons=True)
            if action == "took":
                self.med.took(dose)
                return Reply(f"🎩 Отлично, Сэр!\n\n✅ {drug.name} — принято в {self.now():%H:%M}", edit=True)
            at = self.med.snooze(dose, self.now())
            return Reply(f"🎩 Хорошо, Сэр! Напомню про {drug.name} в {at:%H:%M}.", edit=True)
        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)

    # ------------------------------------------------------------ напоминания (каждую минуту)
    def collect_med_reminders(self) -> list[Reply]:
        out = []
        for dose, drug, case in self.med.due(self.now()):
            rows = [[("✅ Принял", f"med:took:{dose.id}"), ("⏰ Через 15 минут", f"med:snooze:{dose.id}")]]
            out.append(Reply(M.reminder(drug, case, self.today()), buttons=rows))
        return out
