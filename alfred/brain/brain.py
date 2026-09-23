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
                log.info("Brain: %s target=%r due=%s new_due=%s (pro=%s)",
                         result.intent, result.target, result.due_date, result.new_due_date, pro)
                return result
            except ParseError as exc:
                log.warning("Brain parse error (pro=%s): %s | raw=%r", pro, exc, raw[:300])
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
