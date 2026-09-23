"""Мозг Альфреда: отправляет сообщение в ИИ и получает проверенный результат."""

import logging
from datetime import date
from typing import Optional

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
                result = parse(raw)
                log.info("Brain: %s (pro=%s)", result.intent, pro)
                return result
            except ParseError as exc:
                log.warning("Brain parse error (pro=%s): %s | raw=%r", pro, exc, raw[:300])
        if failures == 2:
            raise BrainUnavailable()
        return BrainResult(intent="UNKNOWN")
