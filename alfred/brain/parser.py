"""Разбор и проверка ответа ИИ. Всё, что не прошло проверку, считается непонятым."""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .prompts import INTENTS

SECTIONS = {"finance", "birthdays", "dossier", "weather"}
EVENT_TYPES = {"lecture", "practice", "training", "doctor", "meeting", "other"}


@dataclass
class BrainResult:
    intent: str
    title: Optional[str] = None
    due_date: Optional[date] = None
    due_when: Optional[str] = None
    target: Optional[str] = None
    new_title: Optional[str] = None
    new_due_date: Optional[date] = None
    new_due_when: Optional[str] = None
    clear_due_date: bool = False
    content: Optional[str] = None
    replace_from: Optional[str] = None
    replace_to: Optional[str] = None
    query: Optional[str] = None
    scope: str = "one"
    force_duplicate: bool = False
    answer: Optional[str] = None
    section: Optional[str] = None
    event_type: Optional[str] = None
    event_title: Optional[str] = None
    event_when: Optional[str] = None
    time_text: Optional[str] = None
    new_event_when: Optional[str] = None
    new_time_text: Optional[str] = None
    location: Optional[str] = None
    discipline: Optional[str] = None
    focus: Optional[str] = None
    comment: Optional[str] = None
    comment_remove: Optional[str] = None
    repeat_text: Optional[str] = None
    until_text: Optional[str] = None
    apply_to: str = "one"


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
        due_when=_str(data.get("due_when")),
        target=_str(data.get("target")),
        new_title=_str(data.get("new_title")),
        new_due_date=_date(data.get("new_due_date")),
        new_due_when=_str(data.get("new_due_when")),
        clear_due_date=data.get("clear_due_date") is True,
        content=_str(data.get("content")),
        replace_from=_str(data.get("replace_from")),
        replace_to=None if replace_to is None else str(replace_to).strip(),
        query=_str(data.get("query")),
        scope="all" if data.get("scope") == "all" else "one",
        force_duplicate=data.get("force_duplicate") is True,
        answer=_str(data.get("answer")),
        section=section if section in SECTIONS else None,
        event_type=_str(data.get("event_type")) if data.get("event_type") in EVENT_TYPES else None,
        event_title=_str(data.get("event_title")),
        event_when=_str(data.get("event_when")),
        time_text=_str(data.get("time_text")),
        new_event_when=_str(data.get("new_event_when")),
        new_time_text=_str(data.get("new_time_text")),
        location=_str(data.get("location")),
        discipline=_str(data.get("discipline")),
        focus=_str(data.get("focus")),
        comment=_str(data.get("comment")),
        comment_remove=_str(data.get("comment_remove")),
        repeat_text=_str(data.get("repeat_text")),
        until_text=_str(data.get("until_text")),
        apply_to="series" if data.get("apply_to") == "series" else "one",
    )
