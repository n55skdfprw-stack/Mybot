"""Простой поиск по тексту без ИИ: учитывает регистр, ё/е и окончания русских слов."""

import re

_STOP = {"про", "что", "как", "где", "это", "мой", "мои", "моё", "моя", "все", "всё", "для", "или",
         "задачу", "задача", "задачи", "дело", "дела", "заметку", "заметка", "заметки", "заметке",
         "его", "её", "ее", "эту", "этот", "эта", "нет", "давай", "ладно", "пожалуйста",
         "сделал", "сделала", "сделано", "выполнил", "выполнила", "выполнено", "готово",
         "отметь", "отметить", "вычеркни", "вычеркнуть", "удали", "удалить", "убери",
         "перенеси", "перенести", "поменяй", "поменять", "переименуй", "измени", "изменить",
         "запиши", "записать", "найди", "покажи", "записывал", "записывала",
         "верни", "верните", "вернуть", "восстанови", "купил", "купила", "зря", "вычеркнул",
         "расход", "расходы", "доход", "доходы", "запись", "записи", "последний", "последнюю", "последние",
         "последних", "последняя", "потратил", "потратила", "был", "была", "было",
         "сегодня", "завтра", "послезавтра", "понедельник", "вторник", "среду", "среда",
         "четверг", "пятницу", "пятница", "субботу", "суббота", "воскресенье", "неделю", "через"}


def normalize(text: str) -> str:
    return text.casefold().replace("ё", "е")


def stems(text: str) -> set[str]:
    words = re.findall(r"[a-zа-я0-9]+", normalize(text))
    # Отрезаем окончания: «корма», «корм», «корму» → «корм».
    return {w[:5] if len(w) > 5 else w for w in words if len(w) >= 3 and w not in _STOP}


def distance(a: str, b: str, limit: int = 3) -> int:
    """Сколько букв надо поменять/добавить/убрать, чтобы из a получить b (расстояние Левенштейна)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def typo_close(a: str, b: str) -> bool:
    """Похожи ли слова с поправкой на опечатку: «домафон» ≈ «домофон», «финнансы» ≈ «финансы».
    Короткие слова (до 4 букв) — только точно: иначе «Дима» спутается с «Дина»."""
    n = min(len(a), len(b))
    if n < 5 or a[0] != b[0]:
        return False
    return distance(a, b, 2) <= (1 if n < 8 else 2)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zа-я0-9]+", normalize(text)) if len(w) >= 3 and w not in _STOP]


def score(query: str, text: str) -> int:
    q = stems(query)
    if not q:
        return 0
    t = stems(text)
    hits = 0
    for qs in q:
        if any(ts.startswith(qs[:4]) or qs.startswith(ts[:4]) for ts in t if len(ts) >= 3):
            hits += 1
    if hits:
        return hits
    # Ничего не нашлось точно — прощаем опечатки и слитное написание («иванпетров», «домафон»).
    tw = _words(text)
    joined = "".join(tw)
    pairs = [tw[i] + tw[i + 1] for i in range(len(tw) - 1)]
    for qw in _words(query):
        if any(typo_close(qw, w) or typo_close(qw[:6], w[:6]) and len(qw) >= 6 and len(w) >= 6 for w in tw):
            hits += 1
        elif len(qw) >= 7 and (qw in joined or any(typo_close(qw, p) for p in pairs)):
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
