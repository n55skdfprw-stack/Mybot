"""Раздел «🎂 Дни рождения»: запись обычной речью, список по ближайшим, напоминания в 12:00."""

import re
from dataclasses import replace
from datetime import timedelta
from typing import Optional

from ..brain.parser import BrainResult
from ..database.birthday_repo import Birthday
from ..database.finance_repo import Person
from ..services import search
from ..services.birthdays import parse_birthday
from ..ui import birthday_texts as B
from ..ui.texts import MONTHS_GEN
from .reply import Reply

BD_BUTTONS = [[("✏️ Изменение/удаление", "bd:edit")]]
BD_INTENTS = {"CREATE_BIRTHDAY", "UPDATE_BIRTHDAY", "DELETE_BIRTHDAY", "SHOW_BIRTHDAYS"}
BD_WORDS = re.compile(r"день\s*рожд|дня\s*рожд|днюх|днем\s*рожд|родил(ся|ась)|\bдр\b")
REMIND_DAYS = ((0, "🥳 Сегодня"), (1, "🎈 Завтра"), (7, "📅 Через неделю"))


class BirthdayMixin:
    # ------------------------------------------------------------ помощники
    def _name(self, person_id: int) -> str:
        p = self.debts.person(person_id)
        return p.full_name if p else "Без имени"

    def _said_birthday(self, r: BrainResult):
        """Дату берём только из слов пользователя. Если ИИ придумал дату, которой в сообщении нет
        («Запиши день рождения Оли» → «послезавтра» из прошлого сообщения), — не верим и переспрашиваем."""
        if self._message:
            return parse_birthday(self._message, self.today())
        return parse_birthday(r.bday_text, self.today())

    def _last_birthday(self) -> Optional[Birthday]:
        ctx = self._ctx()
        if ctx.entity_type == "birthday" and ctx.entity_id:
            return self.birthdays.get(ctx.entity_id)
        return None

    def _ambiguous_birthdays(self, r: BrainResult, items: list[Birthday]) -> Reply:
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка",
                                           "choose": "birthday"})
        today = self.today()
        rows = [[(B.line(self._name(b.person_id), b, today)[:60], f"pick:{b.id}")] for b in items[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply("🎩 Сэр, уточните, пожалуйста, чей именно день рождения?", buttons=rows)

    def _find_birthday(self, r: BrainResult) -> Birthday | Reply | None:
        if r.person:
            person = self._resolve_person(r, r.intent, create=False)
            if isinstance(person, Reply):
                return person
            b = self.birthdays.of(person.id)
            return b or Reply(f"🎩 Сэр, дня рождения человека по имени {person.full_name} у меня нет!")
        last = self._last_birthday()
        if last:
            return last
        items = self.birthdays.all(self.today())
        if len(items) == 1:
            return items[0]
        return self._ambiguous_birthdays(r, items) if items else None

    # ------------------------------------------------------------ защита от ошибок ИИ
    def _guard_birthday(self, r: BrainResult, text: str) -> BrainResult:
        t = search.normalize(text)
        correction = re.search(r"\bне\s+\d", t)
        parsed = parse_birthday(text, self.today())
        if r.intent == "CREATE_BIRTHDAY" and correction:
            return replace(r, intent="UPDATE_BIRTHDAY")
        # «У мамы день рождения 12 марта» — это день рождения, а не дело или событие.
        if r.intent not in BD_INTENTS and BD_WORDS.search(t) and parsed and r.intent not in ("ANSWER", "CANCEL"):
            return replace(r, intent="UPDATE_BIRTHDAY" if correction else "CREATE_BIRTHDAY", bday_text=text)
        # «Не 12, а 14 марта» сразу после дня рождения — исправляем его.
        ctx = self._ctx()
        if ctx.entity_type == "birthday" and ctx.entity_id and r.target in (None, "LAST") and not r.person:
            if r.intent.startswith("UPDATE_") and r.intent != "UPDATE_BIRTHDAY" and parsed:
                return replace(r, intent="UPDATE_BIRTHDAY", bday_text=text)
            if r.intent.startswith("DELETE_") and r.intent != "DELETE_BIRTHDAY":
                return replace(r, intent="DELETE_BIRTHDAY")
        return r

    # ------------------------------------------------------------ действия
    def _create_birthday(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        if not r.person and not chosen:
            return self._ask(r, "person", "🎩 Разумеется, Сэр! Чей это день рождения?")
        parsed = self._said_birthday(r)
        if not parsed:
            return self._ask(r, "bday_text", "🎩 Разумеется, Сэр! Какого числа день рождения?")
        person = chosen or self._resolve_person(r, r.intent, create=True)
        if isinstance(person, Reply):
            return person
        before = self.birthdays.of(person.id)
        day, month, year = parsed
        b = self.birthdays.save(person.id, day, month, year or (before.year if before else None))
        self._set_last("birthday", b.id)
        head = "🎩 Записал, Сэр!"
        if before and (before.day, before.month) != (b.day, b.month):
            head = "🎩 Готово, Сэр! Исправил день рождения!"
        return Reply(f"{head}\n\n{B.line(person.full_name, b, self.today())}")

    def _update_birthday(self, r: BrainResult, chosen: Optional[Birthday | Person] = None) -> Reply:
        if isinstance(chosen, Person):
            b = self.birthdays.of(chosen.id)
            if not b:
                return Reply(f"🎩 Сэр, дня рождения человека по имени {chosen.full_name} у меня нет!", edit=True)
        else:
            b = chosen or self._find_birthday(r)
        if isinstance(b, Reply):
            return b
        if not b:
            return Reply("🎩 Сэр, дней рождения пока нет!")
        name = self._name(b.person_id)
        parsed = self._said_birthday(r)
        if not parsed:
            self._set_last("birthday", b.id)
            return self._ask(r, "bday_text", f"🎩 Сэр, какая правильная дата?\n\n{B.line(name, b, self.today())}")
        day, month, year = parsed
        saved = self.birthdays.save(b.person_id, day, month, year or b.year)
        self._set_last("birthday", saved.id)
        return Reply(f"🎩 Готово, Сэр! Исправил день рождения!\n\n{B.line(name, saved, self.today())}")

    def _delete_birthday(self, r: BrainResult, chosen: Optional[Birthday | Person] = None) -> Reply:
        if isinstance(chosen, Person):
            b = self.birthdays.of(chosen.id)
            if not b:
                return Reply(f"🎩 Сэр, дня рождения человека по имени {chosen.full_name} у меня нет!", edit=True)
        else:
            b = chosen or self._find_birthday(r)
        if isinstance(b, Reply):
            return b
        if not b:
            return Reply("🎩 Сэр, дней рождения пока нет!")
        self.birthdays.delete(b)
        self._set_last("birthday", None)
        return Reply(f"🎩 Удалил, Сэр!\n\n❌ {self._name(b.person_id)} — {B.bday_date(b)}")

    def _show_birthdays(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        if not r.person and not chosen:
            return self.birthdays_view()
        person = chosen or self._resolve_person(r, r.intent, create=False)
        if isinstance(person, Reply):
            return person
        b = self.birthdays.of(person.id)
        if not b:
            return Reply(f"🎩 Сэр, дня рождения человека по имени {person.full_name} у меня нет!")
        self._set_last("birthday", b.id)
        return Reply(f"🎩 Разумеется, Сэр!\n\n{B.line(person.full_name, b, self.today())}")

    # ------------------------------------------------------------ экраны
    def birthdays_view(self, edit: bool = False) -> Reply:
        today = self.today()
        items = self.birthdays.all(today)
        if not items:
            return Reply("🎩 Сэр, дней рождения пока нет!\n\nНапример: «У мамы день рождения 12 марта».",
                         edit=edit)
        lines = "\n".join(B.line(self._name(b.person_id), b, today) for b in items[:40])
        return Reply(f"🎩 Дни рождения, Сэр!\n\n{lines}", buttons=BD_BUTTONS, edit=edit)

    def birthdays_edit_view(self, edit: bool = True) -> Reply:
        today = self.today()
        items = self.birthdays.all(today)
        if not items:
            return self.birthdays_view(edit=edit)
        rows = [[(f"❌ {self._name(b.person_id)} — {B.bday_date(b)}"[:60], f"bd:del:{b.id}")] for b in items[:20]]
        rows.append([("↩️ Назад", "bd:list")])
        return Reply("🎩 Какой день рождения удалить, Сэр?\n\nЧтобы изменить — просто напишите, например: "
                     "«У Сергея день рождения не 5, а 6 мая».", buttons=rows, edit=edit)

    def birthday_callback(self, parts: list[str]) -> Reply:
        if parts[1] == "list":
            return self.birthdays_view(edit=True)
        if parts[1] == "edit":
            return self.birthdays_edit_view()
        if parts[1] == "del" and len(parts) == 3 and parts[2].isdigit():
            b = self.birthdays.get(int(parts[2]))
            if b:
                self.birthdays.delete(b)
            return self.birthdays_edit_view()
        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)

    # ------------------------------------------------------------ напоминание в 12:00
    def birthday_reminder(self) -> Optional[Reply]:
        """Одно сообщение на всех: сегодня, завтра и через неделю. None — напоминать не о ком."""
        today = self.today()
        blocks = []
        for shift, head in REMIND_DAYS:
            day = today + timedelta(days=shift)
            found = sorted(self.birthdays.on(day), key=lambda b: self._name(b.person_id))
            if not found:
                continue
            names = "\n".join(B.reminder_line(self._name(b.person_id), b, today) for b in found)
            date_txt = "" if shift == 0 else f", {day.day} {MONTHS_GEN[day.month - 1]}"
            blocks.append(f"{head}{date_txt}:\n{names}")
        if not blocks:
            return None
        return Reply("🎩 Сэр, позвольте напомнить о днях рождения!\n\n" + "\n\n".join(blocks))
