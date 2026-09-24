"""Тексты раздела «Досье»."""

from typing import Optional

from ..database.dossier_repo import Card
from ..services.dossier import lines

LABELS = {"phone": "телефон", "address": "адрес", "job": "работу", "first_name": "имя", "last_name": "фамилию",
          "interests": "интересы", "preferences": "предпочтения", "facts": "важные факты",
          "likes": "что любит", "dislikes": "что не любит"}


def _join(values: list[str]) -> str:
    return ", ".join(v[:1].lower() + v[1:] for v in values)


def card(c: Card, extra: list[str]) -> str:
    """Полная карточка человека. extra — строки про день рождения и долги."""
    main = [f"Имя: {c.first_name}"]
    if c.last_name:
        main.append(f"Фамилия: {c.last_name}")
    if c.phone:
        main.append(f"📞 {c.phone}")
    if c.address:
        main.append(f"📍 {c.address}")
    if c.job:
        main.append(f"💼 {c.job}")

    personal = []
    if lines(c.interests):
        personal.append(f"⭐ Интересы: {_join(lines(c.interests))}")
    if lines(c.preferences):
        personal.append(f"🎯 Предпочтения: {_join(lines(c.preferences))}")
    likes = [x[2:] for x in lines(c.likes_dislikes) if x.startswith("+ ")]
    dislikes = [x[2:] for x in lines(c.likes_dislikes) if x.startswith("- ")]
    if likes:
        personal.append(f"👍 Любит: {_join(likes)}")
    if dislikes:
        personal.append(f"👎 Не любит: {_join(dislikes)}")
    personal += [f"📌 {x}" for x in lines(c.important_facts)]

    parts = [f"🎩 Досье, Сэр!\n\n👤 {c.full_name}", "\n".join(main)]
    parts.append("💬 Личная информация\n" + ("\n".join(personal) if personal else "Пока ничего не записано"))
    if extra:
        parts.append("\n".join(extra))
    return "\n\n".join(parts)


def changed(keys: list[str], remove: bool) -> str:
    what = ", ".join(dict.fromkeys(LABELS[k] for k in keys))
    return f"{'Убрал' if remove else 'Записал'}: {what}"


def short_line(c: Card) -> str:
    tail = f" · {c.job}" if c.job else ""
    return f"👤 {c.full_name}{tail}"[:60]


def empty_hint() -> Optional[str]:
    return "Например: «Создай досье на Сергея Афанасьева» или «Запиши номер Сергея +7 900 123-45-67»."
