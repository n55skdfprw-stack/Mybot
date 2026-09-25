"""Мозг Альфреда: отправляет сообщение в ИИ и получает проверенный результат."""

import logging
from datetime import date
from typing import Optional

from dataclasses import replace

from .dates import find_dates, parse_date
from .llm_client import LLMClient, LLMError
from .parser import BrainResult, ParseError, parse
from .prompts import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger(__name__)


class BrainUnavailable(Exception):
    pass


class Brain:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def analyze(self, text: str, today: date, last_object: Optional[str], pending: Optional[str],
                      task_titles: list[str], note_titles: list[str]) -> BrainResult:
        prompt = build_user_prompt(text, today, last_object, pending, task_titles, note_titles)
        failures = 0
        # Сначала быстрая модель; если её ответ не удалось разобрать — одна попытка на более умной.
        for pro in (False, True):
            try:
                raw = await self.llm.complete(SYSTEM_PROMPT, prompt, pro=pro)
            except LLMError:
                failures += 1
                continue
            try:
                result = resolve_dates(parse(raw), text, today)
                # В логах — только тип команды, без слов пользователя: логи видит владелец, а данные гостей — личные.
                log.info("Brain: %s (pro=%s)", result.intent, pro)
                return result
            except ParseError as exc:
                log.warning("Brain parse error (pro=%s): %s | ответ ИИ: %s символов", pro, exc, len(raw or ""))
        if failures == 2:
            raise BrainUnavailable()
        return BrainResult(intent="UNKNOWN")


def resolve_dates(r: BrainResult, text: str, today: date) -> BrainResult:
    """Даты считает код по словам пользователя. Даты, «придуманные» ИИ, не используются."""
    due = parse_date(r.due_when, today)
    new_due = parse_date(r.new_due_when, today)

    # Страховка: ИИ не процитировал дату, но в сообщении она ровно одна.
    in_text = find_dates(text, today)
    if len(in_text) == 1:
        if r.intent == "CREATE_TASK" and not due:
            due = in_text[0]
        if r.intent == "UPDATE_TASK" and not new_due and not r.new_title:
            new_due = in_text[0]

    title = r.title
    if title and r.due_when and r.due_when.casefold() in title.casefold():
        idx = title.casefold().find(r.due_when.casefold())
        title = (title[:idx] + title[idx + len(r.due_when):]).strip(" ,.-—") or r.title

    return replace(r, title=title, due_date=due, new_due_date=new_due)


GIFT_PROMPT = """Ты — Альфред, заботливый дворецкий. Помоги выбрать подарок на день рождения.
Предложи ровно 5 идей подарка.
Учитывай возраст и пол. Пол определи по имени или по слову («мама», «бабушка» — женщина; «папа», «дедушка» — мужчина);
если пол неясен — предлагай то, что подойдёт любому.
Главное — то, что известно о человеке: интересы, что любит, работа, факты. НИКОГДА не предлагай то, что он не любит.
Разнообразь идеи: что-то для хобби, практичное, впечатление (поход, мастер-класс, билеты), что-то недорогое и милое.
Формат: 5 строк, каждая — эмодзи, подарок, тире, коротко почему (до 12 слов).
Без вступления, без заключения, без нумерации и без звёздочек."""


EMOJI = r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"


def clean_ideas(raw: str) -> list[str]:
    """Убираем нумерацию, звёздочки, лишние значки в середине — оставляем до 5 строк идей."""
    import re
    out = []
    for line in raw.splitlines():
        line = re.sub(r"^[\s>*#\-•]*(?:\d+[.)]\s*)?", "", line).replace("**", "").replace("__", "").strip()
        m = re.match(rf"^((?:{EMOJI})+)\s*(.*)$", line)
        icon, rest = (m.group(1), m.group(2)) if m else ("🎁", line)
        rest = re.sub(EMOJI, "", rest)                       # «Живой пион 🌸—» → «Живой пион —»
        rest = re.sub(r"\s*[—–-]\s+", " — ", rest, count=1).strip(" —")
        rest = re.sub(r"\s{2,}", " ", rest)
        if len(rest) >= 4:
            out.append(f"{icon} {rest}"[:160])
    return out[:5]


async def gift_ideas(llm: LLMClient, profile: str, already: Optional[list[str]] = None) -> list[str]:
    if already:
        profile += ("\n\nЭти идеи уже предлагал — придумай 5 СОВСЕМ ДРУГИХ, ни одну не повторяй и не перефразируй:\n"
                    + "\n".join(already[-15:]))
    try:
        return clean_ideas(await llm.complete(GIFT_PROMPT, profile))
    except LLMError:
        log.exception("Gift ideas failed")
        return []
