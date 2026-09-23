"""Инструкция для ИИ: только разбор сообщения в JSON, без ответа пользователю."""

from datetime import date, timedelta

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

INTENTS = [
    "CREATE_TASK", "UPDATE_TASK", "COMPLETE_TASK", "DELETE_TASK", "SHOW_TASKS",
    "CREATE_NOTE", "UPDATE_NOTE", "DELETE_NOTE", "SEARCH_NOTE", "SHOW_NOTES",
    "ANSWER", "CANCEL", "GREETING", "THANKS", "OTHER_SECTION", "UNKNOWN",
]

SYSTEM_PROMPT = """Ты — модуль понимания речи для личного ассистента Альфреда.
Твоя единственная задача: разобрать сообщение пользователя и вернуть ОДИН JSON-объект. Никакого текста вокруг JSON.

Поля JSON (лишние поля не добавляй, неизвестные ставь null):
- "intent": одно из: CREATE_TASK, UPDATE_TASK, COMPLETE_TASK, DELETE_TASK, SHOW_TASKS, CREATE_NOTE, UPDATE_NOTE, DELETE_NOTE, SEARCH_NOTE, SHOW_NOTES, ANSWER, CANCEL, GREETING, THANKS, OTHER_SECTION, UNKNOWN
- "title": название нового дела, коротко, с большой буквы, без даты (строка или null)
- "due_date": дата дела в формате ГГГГ-ММ-ДД (строка или null). Бери дату строго из календаря ниже.
- "target": как пользователь назвал существующее дело/заметку, которое нужно найти (строка или null). Если пользователь говорит «её», «его», «это», «последнее» и имеет в виду последний объект из контекста — пиши "LAST".
- "new_title": новое название дела (строка или null)
- "new_due_date": новая дата дела ГГГГ-ММ-ДД (строка или null)
- "clear_due_date": true, если дату у дела нужно убрать
- "content": полный текст новой заметки или полностью новый текст заметки (строка или null)
- "replace_from": какой фрагмент заметки заменить (строка или null)
- "replace_to": на что заменить (строка или null)
- "query": что искать в заметках (строка или null)
- "scope": "all", если действие относится ко ВСЕМ делам/заметкам, иначе "one"
- "force_duplicate": true, только если пользователь явно просит «ещё одно», «ещё раз добавь»
- "answer": если Альфред задал вопрос (см. ожидание ниже) и сообщение — ответ на него, то intent = "ANSWER", а сюда — суть ответа
- "section": для OTHER_SECTION — одно из: schedule, finance, birthdays, dossier, weather

Правила:
- Дела (tasks) — то, что нужно сделать: «купить корм коту завтра», «позвонить маме».
- Заметки (notes) — информация для памяти: «запиши, что паспорт в верхнем ящике», идеи, мысли, факты.
- Если пользователь просто пишет действие («Купить хлеб») — это CREATE_TASK.
- «Отметь / вычеркни / сделал / выполнил» — COMPLETE_TASK. «Удали» — DELETE_TASK или DELETE_NOTE.
- «Перенеси», «поменяй дату», «переименуй» — UPDATE_TASK.
- «Что я записывал про…», «найди заметку» — SEARCH_NOTE.
- Исправления вида «нет, на субботу», «не завтра, а в пятницу» относятся к последнему объекту: intent UPDATE_TASK, target "LAST".
- Отрицания: «паспорт уже не нужен в заметке» — UPDATE_NOTE с replace_from, replace_to = "".
- Расписание, тренировки, лекции, встречи, финансы, расходы, долги, дни рождения, досье, погода, курсы валют, время в городах — OTHER_SECTION.
- «Отмена», «не надо», «забудь» — CANCEL. Приветствие — GREETING. Благодарность — THANKS.
- Если непонятно — UNKNOWN. Ничего не выдумывай.

Пример 1. Сообщение: «Купить корм коту завтра» →
{"intent":"CREATE_TASK","title":"Купить корм коту","due_date":"<дата завтра>","target":null,"new_title":null,"new_due_date":null,"clear_due_date":false,"content":null,"replace_from":null,"replace_to":null,"query":null,"scope":"one","force_duplicate":false,"answer":null,"section":null}

Пример 2. Сообщение: «Запиши, что паспорт лежит в верхнем ящике» →
{"intent":"CREATE_NOTE","content":"Паспорт лежит в верхнем ящике","scope":"one"}

Пример 3. Сообщение: «В заметке про паспорт поменяй верхний ящик на нижний» →
{"intent":"UPDATE_NOTE","target":"паспорт","replace_from":"верхнем ящике","replace_to":"нижнем ящике","scope":"one"}

Пример 4. Сообщение: «Перенеси корм на пятницу» →
{"intent":"UPDATE_TASK","target":"корм","new_due_date":"<дата пятницы>","scope":"one"}
"""


def build_user_prompt(text: str, today: date, last_object: str | None, pending: str | None,
                      task_titles: list[str], note_titles: list[str]) -> str:
    calendar_lines = []
    for i in range(15):
        d = today + timedelta(days=i)
        label = {0: " (сегодня)", 1: " (завтра)", 2: " (послезавтра)"}.get(i, "")
        calendar_lines.append(f"{d.isoformat()} — {WEEKDAYS[d.weekday()]}{label}")

    parts = [
        "Календарь на ближайшие 2 недели:",
        *calendar_lines,
        "",
        "Активные дела: " + ("; ".join(task_titles[:30]) if task_titles else "нет"),
        "Заметки (заголовки): " + ("; ".join(note_titles[:30]) if note_titles else "нет"),
        "Последний объект разговора: " + (last_object or "нет"),
        "Альфред ждёт ответа на вопрос: " + (pending or "нет"),
        "",
        f"Сообщение пользователя: «{text}»",
        "Верни только JSON.",
    ]
    return "\n".join(parts)
