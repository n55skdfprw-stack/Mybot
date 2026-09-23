"""Простой поиск по тексту без ИИ: учитывает регистр, ё/е и окончания русских слов."""

import re

_STOP = {"про", "что", "как", "где", "это", "мой", "мои", "моё", "моя", "все", "всё", "для", "или",
         "задачу", "задача", "дело", "дела", "заметку", "заметка", "заметки"}


def normalize(text: str) -> str:
    return text.casefold().replace("ё", "е")


def stems(text: str) -> set[str]:
    words = re.findall(r"[a-zа-я0-9]+", normalize(text))
    # Отрезаем окончания: «корма», «корм», «корму» → «корм».
    return {w[:5] if len(w) > 5 else w for w in words if len(w) >= 3 and w not in _STOP}


def score(query: str, text: str) -> int:
    q = stems(query)
    if not q:
        return 0
    t = stems(text)
    hits = 0
    for qs in q:
        if any(ts.startswith(qs[:4]) or qs.startswith(ts[:4]) for ts in t if len(ts) >= 3):
            hits += 1
    return hits


def best_matches(query: str, items: list, text_of) -> list:
    """Возвращает объекты с наилучшим совпадением (может быть несколько — тогда нужна неоднозначность)."""
    if not query:
        return []
    exact = [it for it in items if normalize(text_of(it)).strip() == normalize(query).strip()]
    if exact:
        return exact
    scored = [(score(query, text_of(it)), it) for it in items]
    top = max((s for s, _ in scored), default=0)
    if top == 0:
        return []
    return [it for s, it in scored if s == top]
