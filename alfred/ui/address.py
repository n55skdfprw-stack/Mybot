"""Обращение «Сэр» или «Мэм»: все тексты Альфреда написаны с «Сэр», при отправке меняем на выбранное."""

import re
from dataclasses import replace
from typing import Optional

from ..core.reply import Reply

SIR, MAM = "Сэр", "Мэм"
ADDRESS_RE = re.compile(r"(?:обращайся|обращайтесь|обращаться|зови|зовите|называй|называйте)\s+"
                        r"(?:ко\s+мне\s+|ко\s+мне,\s*|мне\s+|меня\s+)?(?:как\s+|на\s+)?[«\"]?(сэр|мэм)\b", re.I)


def personalize(reply: Reply, address: Optional[str]) -> Reply:
    if address != MAM:
        return reply
    swap = lambda s: s.replace(SIR, MAM).replace(SIR.lower(), MAM.lower())   # noqa: E731
    buttons = [[(swap(t), d) for t, d in row] for row in reply.buttons] if reply.buttons else reply.buttons
    return replace(reply, text=swap(reply.text), buttons=buttons,
                   extra=[personalize(e, address) for e in reply.extra])


def asked_address(text: str) -> Optional[str]:
    """«Обращайся ко мне Мэм» → «Мэм»."""
    m = ADDRESS_RE.search(text or "")
    return (SIR if m.group(1).lower() == "сэр" else MAM) if m else None
