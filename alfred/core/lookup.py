"""«Всё про человека» и поиск сразу по всем разделам — без ИИ.

«Что у меня про Сергея?», «Всё про Сергея», «Что я знаю о Диме?» → досье, день рождения, долги
и все дела, встречи, заметки и траты, где он упоминается.
«Найди везде домофон», «Всё про машину» → совпадения из каждого раздела.
"""

import re
from datetime import timedelta
from typing import Optional

from ..brain.dates import find_dates
from ..services import search
from ..services.finance import _forms
from ..ui import texts as T
from ..ui import dossier_texts as DT
from ..ui import finance_texts as F
from ..ui import schedule_texts as S
from .reply import ProtectedReply as Reply

LOOKUP_RES = [
    re.compile(r"^(?:альфред,?\s*)?(?:что\s+(?:у\s+меня\s+)?(?:есть\s+|записано\s+)?|что\s+(?:я|ты)\s+знаю?\w*\s+|"
               r"(?:найди|покажи|поищи|расскажи)\s+(?:мне\s+)?(?:все|всё|везде)\s+|(?:все|всё)\s+)"
               r"(?:про|о|об|обо)\s+(.{2,60}?)[\s?.!]*$"),
    re.compile(r"^(?:альфред,?\s*)?(?:найди|поищи|ищи)\s+везде\s+(?:про\s+|о\s+|об\s+)?(.{2,60}?)[\s?.!]*$"),
]
# Если человек назвал раздел — пусть работает обычный поиск этого раздела
SECTION_RE = re.compile(r"\bв\s+(?:замет|дел|задач|финанс|расход|доход|распоряд|расписан|досье|медкарт|дн\w*\s+рожд)|"
                        r"погод|курс|должен|долг|сколько")
SKIP = {"себе", "тебе", "себя", "мне", "меня", "нас", "вас", "нем", "ней", "них", "этом", "том"}
NOT_A_TOPIC = {"деньги", "финансы", "расходы", "доходы", "траты", "дела", "задачи", "заметки", "распорядок",
               "расписание", "пары", "лекции", "погоду", "погода", "день рождения", "дни рождения", "медкарту",
               "здоровье", "неделю", "месяц", "выходные"}
EACH = 5


def _clean(query: str) -> str:
    q = re.sub(r"\b(?:у\s+меня|мой|моя|мое|моё|мои|моем|моей|моих)\b", " ", query)
    return re.sub(r"\s+", " ", q).strip(" ,.?!«»\"")


def _name_hits(forms: set[str], text: str) -> bool:
    """Есть ли в тексте имя в любом падеже: «Серёге», «Сергея», «с Димой»."""
    for word in re.findall(r"[а-яa-z]+", search.normalize(text)):
        for f in forms:
            stem = f[:-1] if len(f) >= 4 else f
            if word.startswith(stem) and len(word) <= len(f) + 3:
                return True
    return False


def _stem(word: str) -> str:
    return re.sub(r"(?:ами|ями|ой|ей|ом|ем|ым|им|ою|ею|ах|ях|а|я|е|у|ю|ы|и|о|ь|й)$", "", word)


def _is_name(word: str, name: str) -> bool:
    """«Сергея» ≈ «Сергей», «Диме» ≈ «Дима», но «машину» ≠ «Маша»."""
    a, b = _stem(word), _stem(name)
    if min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)) and abs(len(a) - len(b)) <= 1:
        return True
    return search.typo_close(word, name)


def _is_person(query: str, p) -> bool:
    words = query.split()
    first = words[0]
    if p.last_name and search.typo_close(first, search.normalize(p.first_name + p.last_name)):
        return True
    names = _forms(search.normalize(p.first_name))
    if p.last_name:
        names.add(search.normalize(p.last_name))
    return all(any(_is_name(w, n) for n in names) for w in words)


def _section(title: str, lines: list[str]) -> Optional[str]:
    if not lines:
        return None
    more = f"\n…и ещё {len(lines) - EACH}" if len(lines) > EACH else ""
    return f"{title}\n" + "\n".join(f"• {x}" for x in lines[:EACH]) + more


class LookupMixin:
    def try_lookup(self, text: str) -> Optional[Reply]:
        t = search.normalize(text).strip()
        if len(t) > 80 or SECTION_RE.search(t):
            return None
        for rx in LOOKUP_RES:
            m = rx.match(t)
            if m:
                break
        else:
            return None
        query = _clean(m.group(1))
        if not query or query in SKIP or len(query) < 2:
            return None
        if query in NOT_A_TOPIC or find_dates(query, self.today()):
            return None          # «что у меня про завтра», «что про деньги» — это расписание / финансы, не поиск
        self._clear_pending()
        # исходное написание (с большой буквы) — чтобы имя в ответе выглядело как у человека
        raw = re.search(re.escape(query), search.normalize(text))
        shown = text[raw.start():raw.end()] if raw else query
        people = [p for p in (self.debts.find_people(shown) if len(query.split()) <= 2 else [])
                  if _is_person(query, p)]
        if len(people) == 1:
            return self.person_everything(people[0].id, edit=False)
        if len(people) > 1:
            rows = [[(f"👤 {p.full_name}", f"who:{p.id}")] for p in people[:10]]
            return Reply("🎩 Сэр, таких людей у меня несколько. О ком речь?", buttons=rows)
        return self.search_everywhere(shown)

    # ------------------------------------------------------------ человек
    def _mentions(self, match) -> list[str]:
        today = self.today()
        parts = []
        tasks = [t.title + (f" (до {t.due_date:%d.%m})" if t.due_date else "")
                 for t in self.tasks.active() if match(t.title)]
        parts.append(_section("📋 Дела", tasks))
        seen_series, events = set(), []
        for e in self.schedule.between(today - timedelta(days=7), today + timedelta(days=90)):
            text = " ".join(x for x in (e.title, e.comment, e.location, e.discipline) if x)
            if not text or not match(text):
                continue
            if e.recurrence_id:
                if e.recurrence_id in seen_series:
                    continue
                seen_series.add(e.recurrence_id)
            what = S.label(e) + (f" «{T.short(e.discipline, 45)}»" if e.discipline else "") \
                + (f", {e.location}" if e.location else "")
            events.append(f"{e.date:%d.%m} {S.time_span(e)} — {what}")
        parts.append(_section("🕰️ Распорядок", events))
        notes = [n.title for n in self.notes.all() if match(f"{n.title} {n.content}")]
        parts.append(_section("📝 Заметки", notes))
        ops = [F.op_line(o) for o in self.finance.latest(limit=300)
               if match(f"{o.category or ''} {o.description or ''}")]
        parts.append(_section("💰 Финансы", ops))
        return [p for p in parts if p]

    def person_everything(self, person_id: int, edit: bool = True) -> Reply:
        person = self.debts.person(person_id)
        if not person:
            return Reply("🎩 Сэр, этого человека уже нет!", edit=edit)
        forms = set()
        for name in (person.first_name,):
            forms |= _forms(search.normalize(name))
        if person.last_name:
            forms.add(search.normalize(person.last_name))
        card = self.dossier.get(person.id)
        if card:
            head = DT.card(card, self._extra(card)).replace("🎩 Досье, Сэр!", "🎩 Сэр, вот всё, что я знаю!", 1)
        else:
            head = f"🎩 Сэр, вот всё, что я знаю!\n\n👤 {person.full_name}"
        mentions = self._mentions(lambda s: _name_hits(forms, s))
        text = head + ("\n\n🔎 Упоминания\n\n" + "\n\n".join(mentions) if mentions else "")
        self._set_last("person", person.id)
        rows = [[("🗂️ Открыть досье", f"dos:show:{person.id}")]]
        b = self.birthdays.of(person.id)
        if b:
            rows.insert(0, [("🎁 Идея подарка", f"bd:gift:{b.id}")])
        return Reply(text, buttons=rows, edit=edit)

    # ------------------------------------------------------------ везде
    def search_everywhere(self, query: str) -> Reply:
        need = max(1, len(search.stems(query)) - 1)
        match = (lambda s: search.score(query, s) >= need)
        parts = self._mentions(match)
        people = [DT.short_line(c) for c in self.dossier.search(query)]
        if people:
            parts.insert(0, _section("🗂️ Досье", people))
        med = [c.title for c in self.med.find(query)]
        med += [f"{x.name}" + (f" · {x.specialty}" if x.specialty else "") for x in self.med.find_contacts(query)]
        if med:
            parts.append(_section("🩺 Медкарта", med))
        if not parts:
            return Reply(f"🎩 Сэр, по запросу «{query}» ничего не нашёл ни в одном разделе!")
        return Reply(f"🎩 Сэр, вот что нашёл по запросу «{query}»!\n\n" + "\n\n".join(parts))
