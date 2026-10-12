"""💬 Вопросы о своих записях обычными словами: «Когда у меня анатомия?», «Во сколько завтра первая пара?»,
«Есть окно между парами в четверг?», «Что надо сделать до пятницы?».

Альфред собирает короткую сводку из распорядка, дел, долгов, финансов и медкарты и просит GigaChat ответить
СТРОГО по ней. Сводку видит только GigaChat в этом одном запросе — никуда больше она не сохраняется.
"""

import logging
import re
from datetime import timedelta
from typing import Optional

from ..brain.llm_client import LLMError
from ..brain.parser import BrainResult
from ..ui import schedule_texts as S
from ..ui import texts as T
from ..ui import finance_texts as F
from .reply import Reply

log = logging.getLogger(__name__)

PAST_DAYS = 14             # «когда я последний раз был у врача» — смотрим и назад
AHEAD_DAYS = 28
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

ANSWER_PROMPT = """Ты — Альфред, вежливый дворецкий. Ответь на вопрос пользователя ТОЛЬКО по его записям ниже.
Пиши по-русски, коротко: 1–3 предложения или короткий список. Обращайся «Сэр».
Даты, время, аудитории и суммы бери ТОЧНО из записей, ничего не выдумывай и не округляй.
Если в записях ответа нет — так и скажи одной фразой.
Без markdown: без звёздочек, решёток и таблиц."""


class AskMixin:
    def _facts(self) -> str:
        """Сводка записей для ответа на вопрос: распорядок, дела, долги, траты за месяц, лечение."""
        now, today = self.now(), self.today()
        lines = [f"Сейчас: {WEEKDAY_SHORT[today.weekday()]} {today:%d.%m.%Y} {now:%H:%M}."]
        events = self.schedule.between(today - timedelta(days=PAST_DAYS), today + timedelta(days=AHEAD_DAYS))
        if events:
            lines.append("Распорядок:")
            for e in events:
                bits = [f"{WEEKDAY_SHORT[e.date.weekday()]} {e.date:%d.%m}", S.time_span(e),
                        f"{e.pair_no}-я пара" if e.pair_no else "", S.label(e).lower(),
                        f"«{e.discipline}»" if e.discipline else "", e.location or "", e.comment or ""]
                lines.append("- " + ", ".join(b for b in bits if b))
        tasks = self.tasks.active()
        if tasks:
            lines.append("Дела:")
            lines += [f"- {t.title}" + (f" (до {t.due_date:%d.%m})" if t.due_date else "") for t in tasks[:30]]
        debts = self.debts.active()
        if debts:
            lines.append("Долги:")
            lines += [f"- {p.full_name} должен пользователю {F.money(d.amount)}" if d.direction == "owes_me"
                      else f"- пользователь должен {p.full_name} {F.money(d.amount)}" for p, d in debts]
        ops = self.finance.between(today.replace(day=1), today)
        if ops:
            by_cat: dict[str, float] = {}
            for o in ops:
                if o.type == "expense":
                    by_cat[o.category or "Другое"] = by_cat.get(o.category or "Другое", 0) + o.amount
            if by_cat:
                lines.append("Расходы с начала месяца по категориям: "
                             + ", ".join(f"{c} {F.money(v)}" for c, v in sorted(by_cat.items(), key=lambda x: -x[1])))
        cases = [c for c in self.med.cases() if c.ended is None]
        if cases:
            lines.append("Лечение сейчас:")
            for c in cases:
                drugs = "; ".join(f"{d.name} {', '.join(d.times)}" for d in c.drugs if not d.stopped)
                lines.append(f"- {c.title}" + (f": {drugs}" if drugs else ""))
        return "\n".join(lines)

    async def _answer_question(self, r: BrainResult, text: str) -> Reply:
        question = text.strip()
        try:
            answer = await self.brain.llm.complete(ANSWER_PROMPT, f"{self._facts()}\n\nВопрос: «{question}»")
        except LLMError:
            return Reply(T.AI_UNAVAILABLE)
        answer = re.sub(r"[*#_`]+", "", answer or "").strip()
        if not answer:
            return Reply(T.NOT_UNDERSTOOD)
        return Reply(answer if answer.startswith("🎩") else f"🎩 {answer}")
