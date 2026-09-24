"""Раздел «🗂️ Досье»: карточки людей. Долги и дни рождения — те же люди, в карточке они видны."""

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

    # ------------------------------------------------------------ защита от ошибок ИИ
    def _guard_dossier(self, r: BrainResult, text: str) -> BrainResult:
        # «Запиши номер Сергея +7 900…» — это досье, а не заметка и не дело.
        if r.intent in ("CREATE_NOTE", "CREATE_TASK", "UNKNOWN") and r.person and find_phone(text):
            return replace(r, intent="UPDATE_PERSON", dossier={"phone": find_phone(text)})
        return r

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
