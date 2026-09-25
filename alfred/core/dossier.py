"""Раздел «🗂️ Досье»: карточки людей. Долги и дни рождения — те же люди, в карточке они видны."""

import re
from dataclasses import replace
from typing import Optional

from ..brain.parser import BrainResult
from ..database.dossier_repo import Card
from ..database.finance_repo import Person
from ..services.dossier import KEYS, find_phone
from ..ui import birthday_texts as B
from ..ui import dossier_texts as DT
from ..ui import finance_texts as F
from .reply import Reply

NAME_WORD = r"([А-ЯЁA-Zа-яёa-z][а-яёa-z\-]+(?:\s+[А-ЯЁA-Z][а-яёa-z\-]+)?)"
RENAME_RES = [
    re.compile(rf"(?:переименуй|поменяй|измени|исправь|замени)\s+(?!имя\b){NAME_WORD}\s+(?:в|на)\s+{NAME_WORD}",
               re.I),                                                                          # кого → как
    re.compile(rf"(?:исправь|измени|поменяй)\s+имя\s+(?:на\s+){NAME_WORD}", re.I),           # последний
    re.compile(rf"(?:правильно|правильное имя|его зовут|её зовут|ее зовут)\s*[:—-]?\s*{NAME_WORD}\s*$", re.I),
    re.compile(rf"имя\s+не\s+\S+,?\s+а\s+{NAME_WORD}", re.I),
]
REVERT_RE = re.compile(r"^(?:поменяй|верни|измени|сделай|переименуй)?\s*(?:всё\s+|все\s+|имя\s+)?"
                       r"(?:обратно|назад|как\s+было)[.!]*$|^отмени\s+(?:переименование|это)[.!]*$", re.I)
LOVE_RE = re.compile(r"люб|нрав|обожа|ненавид|терпеть|бесит|раздража|фанат|в\s+восторге", re.I)
MOVE_NOTE_RE = re.compile(r"(?:из\s+замет\w*|заметк\w*).*(?:в\s+досье)|(?:в\s+досье).*(?:из\s+замет\w*|заметк\w*)",
                          re.I)
MOVE_LAST_RE = re.compile(r"^(?:перенеси|перемести|запиши|напиши|добавь|сохрани)\s+(?:это\s+|её\s+|ее\s+|его\s+)?"
                          r"(?:лучше\s+)?в\s+досье[.!]*$", re.I)

DOS_INTENTS = {"CREATE_PERSON", "UPDATE_PERSON", "DELETE_PERSON", "SHOW_PERSON", "SEARCH_PEOPLE"}
LIST_LIMIT = 10


class DossierMixin:
    # ------------------------------------------------------------ помощники
    def _changes(self, r: BrainResult) -> dict:
        changes = {k: v for k, v in (r.dossier or {}).items() if k in KEYS and v}
        phone = find_phone(getattr(self, "_raw_text", "") or self._message)
        if phone and (r.intent == "UPDATE_PERSON" or "phone" in changes):
            changes["phone"] = phone
        return changes

    def _extra(self, card: Card) -> list[str]:
        out = []
        b = self.birthdays.of(card.id)
        if b:
            out.append(f"🎂 День рождения: {B.bday_date(b)} · {B.when(b, self.today())}")
        person = Person(card.id, card.first_name, card.last_name)
        for direction in ("owes_me", "i_owe"):
            d = self.debts.get(person, direction)
            if d:
                out.append(f"🤝 Должен вам: {F.money(d.amount)}" if direction == "owes_me"
                           else f"💸 Вы должны: {F.money(d.amount)}")
        return out

    def _last_card(self) -> Optional[Card]:
        ctx = self._ctx()
        if ctx.entity_type == "person" and ctx.entity_id:
            return self.dossier.get(ctx.entity_id)
        return None

    def _find_card(self, r: BrainResult) -> Card | Reply | None:
        """Человек по имени, иначе — последний, о ком шла речь."""
        if not r.person:
            last = self._last_card()
            return last or self._ask(r, "person", "🎩 Сэр, о ком идёт речь?")
        people = self.debts.find_people(r.person)
        if not people:
            return None
        if len(people) > 1:
            return self._resolve_person(r, r.intent, create=False)   # покажет кнопки выбора
        return self.dossier.get(people[0].id)

    def _card_of(self, chosen) -> Optional[Card]:
        return self.dossier.get(chosen.id) if chosen is not None else None

    # ------------------------------------------------------------ без ИИ: переименовать, перенести заметку
    def _last_person_id(self) -> Optional[int]:
        """О ком говорили последним: досье, день рождения или долг."""
        ctx = self._ctx()
        if not ctx.entity_id:
            return None
        if ctx.entity_type == "person":
            return ctx.entity_id
        if ctx.entity_type == "birthday":
            b = self.birthdays.get(ctx.entity_id)
            return b.person_id if b else None
        if ctx.entity_type == "debt":
            d = self.debts.by_id(ctx.entity_id)
            return d.person_id if d else None
        return None

    def try_rename(self, text: str) -> Optional[Reply]:
        """«Исправь имя на Даня», «Переименуй Дани в Даня», «Имя не Дани, а Даня», «Поменяй обратно»."""
        last = self.__dict__.get("_last_rename")
        if last and REVERT_RE.search(text.strip()):
            pid, old = last
            card = self.dossier.get(pid)
            if card:
                was = card.full_name
                parts = old.split(maxsplit=1)
                card, _ = self.dossier.apply(card, {"first_name": parts[0]})
                if len(parts) > 1:
                    card, _ = self.dossier.apply(card, {"last_name": parts[1]})
                elif card.last_name:
                    self.dossier.repo.set(self.user_id, card.id, {"last_name": None})
                    card = self.dossier.get(card.id)
                self.__dict__["_last_rename"] = (card.id, was)
                self._set_last("person", card.id)
                return Reply(f"🎩 Готово, Сэр! Вернул как было!\n\n👤 {was} → {card.full_name}")
        for i, rx in enumerate(RENAME_RES):
            m = rx.search(text.strip().rstrip("!."))
            if not m:
                continue
            if i == 0:
                found = self.debts.find_people(m.group(1))
                if len(found) != 1:
                    return None          # это не человек (например, «Переименуй дело …») — пусть разбирается ИИ
                pid, new = found[0].id, m.group(2)
            else:
                pid, new = self._last_person_id(), m.group(1)
                if not pid:
                    return Reply("🎩 Сэр, чьё имя исправить? Напишите, например: «Переименуй Дани в Даня».")
            card = self.dossier.get(pid)
            if not card:
                return None
            old = card.full_name
            parts = new.split()
            changes = {"first_name": parts[0]}
            if len(parts) > 1:
                changes["last_name"] = parts[1]
            card, _ = self.dossier.apply(card, changes)
            self._set_last("person", card.id)
            self.__dict__["_last_rename"] = (card.id, old)            # для «Поменяй обратно»
            extra = []
            b = self.birthdays.of(card.id)
            if b:
                extra.append(B.line(card.full_name, b, self.today()))
            return Reply(f"🎩 Готово, Сэр! Исправил имя!\n\n👤 {old} → {card.full_name}"
                         + ("\n" + "\n".join(extra) if extra else ""))
        return None

    async def try_move_note(self, text: str) -> Optional[Reply]:
        """«Удали из заметок и запиши в досье» — переносим последнюю заметку в досье."""
        ctx = self._ctx()
        right_after_note = ctx.entity_type == "note" and MOVE_LAST_RE.search(text.strip())
        if not (MOVE_NOTE_RE.search(text) or right_after_note):
            return None
        note = self.notes.get(ctx.entity_id) if ctx.entity_type == "note" and ctx.entity_id else None
        if note is None:
            notes = self.notes.all()
            note = notes[-1] if notes else None
        if note is None:
            return Reply("🎩 Сэр, заметок нет — переносить нечего! Напишите сразу, например: "
                         "«Запиши в досье Васи: …».")
        if self.quota:
            refusal = self.quota()
            if refusal:
                return Reply(refusal)
        try:
            r = await self.brain.analyze(text=f"Запиши в досье: {note.content}", today=self.today(),
                                         last_object=None, pending=None, task_titles=[], note_titles=[])
        except Exception:
            return Reply("🎩 Прошу прощения, Сэр! Не удалось разобрать заметку — попробуйте чуть позже.")
        if r.intent not in ("UPDATE_PERSON", "CREATE_PERSON") or not r.person:
            return Reply(f"🎩 Сэр, не понял, о ком эта заметка. Напишите так: «Запиши в досье Васи: {note.content}».")
        if not r.dossier:
            r = replace(r, dossier={"facts": note.content})
        self._message = self._raw_text = note.content
        reply = self._update_person(replace(r, intent="UPDATE_PERSON"), create=True)
        self.notes.delete([note])
        body = reply.text.split("\n\n", 1)[1] if "\n\n" in reply.text else ""
        return Reply(f"🎩 Готово, Сэр! Перенёс заметку в досье.\n\n{body}")

    # ------------------------------------------------------------ защита от ошибок ИИ
    def _guard_dossier(self, r: BrainResult, text: str) -> BrainResult:
        # «Запиши номер Сергея +7 900…» — это досье, а не заметка и не дело.
        if r.intent in ("CREATE_NOTE", "CREATE_TASK", "UNKNOWN") and r.person and find_phone(text):
            return replace(r, intent="UPDATE_PERSON", dossier={"phone": find_phone(text)})
        # «Диани зануда»: ИИ записал в «не любит», хотя про любовь ни слова — это просто факт о человеке.
        if r.intent == "UPDATE_PERSON" and r.dossier and not r.dossier_remove \
                and set(r.dossier) <= {"likes", "dislikes"} and not LOVE_RE.search(text):
            return replace(r, dossier={"facts": "; ".join(v for v in r.dossier.values() if v)})
        # «Иннокентий зануда», «Вася Пупкин — должник»: о человеке из досье — пишем в досье, а не в заметки.
        if r.intent in ("CREATE_NOTE", "UNKNOWN", "UPDATE_BIRTHDAY"):
            about = self._about_known_person(text)
            if about:
                person, fact = about
                return replace(r, intent="UPDATE_PERSON", person=person, dossier={"facts": fact},
                               dossier_remove=False)
        return r

    def _about_known_person(self, text: str):
        """«Иннокентий гандон, так и запиши» → («Иннокентий», «гандон»), если Иннокентий уже есть у Альфреда."""
        t = re.sub(r",?\s*(так\s+и\s+)?запиши\w*[.!]*$", "", text.strip(), flags=re.I).strip(" .!")
        words = t.split()
        if not 2 <= len(words) <= 8:
            return None
        for n in (2, 1):
            name = " ".join(words[:n])
            if not name[:1].isupper():
                continue
            people = self.debts.find_people(name)
            if len(people) == 1 and len(words) > n:
                fact = " ".join(words[n:]).lstrip("—-–: ").strip()
                if fact:
                    return people[0].full_name, fact
        return None

    # ------------------------------------------------------------ действия
    def _create_person(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        if not r.person and not chosen:
            return self._ask(r, "person", "🎩 Разумеется, Сэр! На кого завести досье?")
        existing = [chosen] if chosen else self.debts.find_people(r.person)
        if len(existing) > 1:
            return self._resolve_person(r, r.intent, create=False)
        changes = self._changes(r)
        if existing:
            card = self.dossier.show(existing[0].id)
            head = "🎩 Сэр, досье на этого человека уже есть!"
        else:
            person = self.debts.create_person(r.person)
            card = self.dossier.show(person.id)
            head = "🎩 Разумеется, Сэр! Досье создано!"
        tail = ""
        if changes:
            card, done = self.dossier.apply(card, changes)
            tail = "\n" + DT.changed(done, False) if done else ""
        self._set_last("person", card.id)
        return Reply(f"{head}\n\n👤 {card.full_name}{tail}")

    def _update_person(self, r: BrainResult, chosen: Optional[Person] = None, create: bool = False) -> Reply:
        changes = self._changes(r)
        if not changes:
            return Reply("🎩 Сэр, что именно записать о человеке?")
        card = self._card_of(chosen) if chosen else self._find_card(r)
        if isinstance(card, Reply):
            return card
        if card is None:
            if not create:
                self._set_pending(r.intent, None, {"result": self._dump(r), "question": "создать досье?",
                                                   "choose": "dossier_create"})
                return Reply(f"🎩 Разумеется, Сэр! Досье на человека по имени {r.person} ещё нет. Создать?",
                             buttons=[[("✅ Создать", "dos:yes")], [("↩️ Отмена", "dos:no")]])
            card = self.dossier.get(self.debts.create_person(r.person).id)
        card, done = self.dossier.apply(card, changes, remove=r.dossier_remove)
        self._set_last("person", card.id)
        if not done:
            return Reply(f"🎩 Сэр, в досье {card.full_name} такого нет!" if r.dossier_remove
                         else "🎩 Сэр, что именно записать о человеке?")
        head = "🎩 Готово, Сэр!" if r.dossier_remove else "🎩 Записал, Сэр!"
        return Reply(f"{head}\n\n👤 {card.full_name}\n{DT.changed(done, r.dossier_remove)}")

    def _show_person(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        if not r.person and not chosen and not self._last_card():
            return self.dossier_view()
        card = self._card_of(chosen) if chosen else self._find_card(r)
        if isinstance(card, Reply):
            return card
        if card is None:
            return Reply(f"🎩 Сэр, в досье нет человека по имени {r.person}!")
        return self.card_view(card.id, edit=False)

    def _delete_person(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        card = self._card_of(chosen) if chosen else self._find_card(r)
        if isinstance(card, Reply):
            return card
        if card is None:
            return Reply(f"🎩 Сэр, в досье нет человека по имени {r.person}!")
        return self._confirm_delete_card(card, edit=bool(chosen))

    def _search_people(self, r: BrainResult) -> Reply:
        query = r.query or r.person or ""
        if not query:
            return self.dossier_view()
        found = self.dossier.search(query)
        if not found:
            return Reply("🎩 Сэр, в досье никого такого не нашёл!")
        if len(found) == 1:
            return self.card_view(found[0].id, edit=False)
        rows = [[(DT.short_line(c), f"dos:show:{c.id}")] for c in found[:LIST_LIMIT]]
        return Reply("🎩 Нашёл несколько человек, Сэр!", buttons=rows)

    # ------------------------------------------------------------ экраны
    def dossier_view(self, edit: bool = False) -> Reply:
        cards = self.dossier.all()
        if not cards:
            return Reply(f"🎩 Сэр, досье пока пусто!\n\n{DT.empty_hint()}", edit=edit)
        if len(cards) > LIST_LIMIT:
            self._set_pending("SEARCH_PEOPLE", "query", {"result": self._dump(BrainResult(intent="SEARCH_PEOPLE")),
                                                         "question": "Кого ищем?"})
            return Reply(f"🎩 Досье, Сэр!\n\n🔎 Кого ищем? Людей в досье: {len(cards)}. "
                         "Введите имя, фамилию или что-то о человеке.", edit=edit)
        rows = [[(DT.short_line(c), f"dos:show:{c.id}")] for c in cards]
        return Reply("🎩 Досье, Сэр!", buttons=rows, edit=edit)

    def card_view(self, person_id: int, edit: bool = True) -> Reply:
        card = self.dossier.get(person_id)
        if not card:
            return self.dossier_view(edit=edit)
        self._set_last("person", card.id)
        rows = [[("🔎 Вернуться к поиску", "dos:list")], [("❌ Удалить досье", f"dos:del:{card.id}")]]
        b = self.birthdays.of(card.id)
        if b:
            rows.insert(0, [("🎁 Идея подарка", f"bd:gift:{b.id}")])
        return Reply(DT.card(card, self._extra(card)), buttons=rows, edit=edit)

    def _confirm_delete_card(self, card: Card, edit: bool = True) -> Reply:
        rows = [[("✅ Да, удалить", f"dos:delyes:{card.id}")], [("↩️ Нет", f"dos:show:{card.id}")]]
        return Reply(f"🎩 Сэр, вы действительно хотите удалить всё досье: {card.full_name}?",
                     buttons=rows, edit=edit)

    def dossier_callback(self, parts: list[str]) -> Reply:
        action = parts[1]
        num = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        if action == "list":
            return self.dossier_view(edit=True)
        if action == "show" and num:
            return self.card_view(num)
        if action == "del" and num:
            card = self.dossier.get(num)
            return self._confirm_delete_card(card) if card else self.dossier_view(edit=True)
        if action == "delyes" and num:
            card = self.dossier.get(num)
            if not card:
                return self.dossier_view(edit=True)
            self.dossier.remove(card)
            self._set_last("person", None)
            return Reply(f"🎩 Удалил досье, Сэр!\n\n❌ {card.full_name}", edit=True)
        if action in ("yes", "no"):
            ctx = self._ctx()
            if not (ctx.intent and ctx.data.get("choose") == "dossier_create"):
                return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)
            r = self._load(ctx.data["result"])
            self._clear_pending()
            if action == "no":
                return Reply("🎩 Как скажете, Сэр! Ничего не записываю.", edit=True)
            return replace(self._update_person(r, create=True), edit=True)
        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)
