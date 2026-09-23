"""Разбор и проверка ответа ИИ. Всё, что не прошло проверку, считается непонятым."""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .prompts import INTENTS

SECTIONS = {"schedule", "finance", "birthdays", "dossier", "weather"}


@dataclass
class BrainResult:
    intent: str
    title: Optional[str] = None
    due_date: Optional[date] = None
    target: Optional[str] = None
    new_title: Optional[str] = None
    new_due_date: Optional[date] = None
    clear_due_date: bool = False
    content: Optional[str] = None
    replace_from: Optional[str] = None
    replace_to: Optional[str] = None
    query: Optional[str] = None
    scope: str = "one"
    force_duplicate: bool = False
    answer: Optional[str] = None
    section: Optional[str] = None


class ParseError(Exception):
    pass


def _str(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _date(value) -> Optional[date]:
    s = _str(value)
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def extract_json(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ParseError("no json object")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ParseError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ParseError("json is not an object")
    return data


def parse(raw: str) -> BrainResult:
    data = extract_json(raw)
    intent = _str(data.get("intent"))
    if intent not in INTENTS:
        raise ParseError(f"unknown intent: {intent}")
    replace_to = data.get("replace_to")
    section = _str(data.get("section"))
    return BrainResult(
        intent=intent,
        title=_str(data.get("title")),
        due_date=_date(data.get("due_date")),
        target=_str(data.get("target")),
        new_title=_str(data.get("new_title")),
        new_due_date=_date(data.get("new_due_date")),
        clear_due_date=data.get("clear_due_date") is True,
        content=_str(data.get("content")),
        replace_from=_str(data.get("replace_from")),
        replace_to=None if replace_to is None else str(replace_to).strip(),
        query=_str(data.get("query")),
        scope="all" if data.get("scope") == "all" else "one",
        force_duplicate=data.get("force_duplicate") is True,
        answer=_str(data.get("answer")),
        section=section if section in SECTIONS else None,
    )
