"""🤖 Робот-проверяльщик: сам пишет настоящему Альфреду в Telegram и проверяет ответы.

Запуск (из папки проекта):  python tools/live_check.py
Первый запуск попросит войти в Telegram (номер телефона и код) — вводите сами, только в этом окне.

Что делает:
- отправляет Альфреду фразы из списка SCENARIOS и ждёт ответ;
- проверяет, что в ответе есть нужные слова;
- всё, что записал, сразу отменяет командой «Отмени последнее» — ваши настоящие записи не трогаются;
- в конце удаляет из чата свои сообщения и ответы на них;
- печатает итог и сохраняет его в tools/live_report.txt.

Нужно: файл tools/live_check.env (образец — tools/live_check.env.example) и библиотека Telethon:
    pip install -r tools/requirements-live.txt

⚠️ Файл live_check.session — это ключ от вашего аккаунта Telegram. Никому его не отправляйте.
   Он лежит не в папке проекта (она в OneDrive), а в %LOCALAPPDATA%\\AlfredLiveCheck — не в облаке и не в GitHub.
   Отозвать доступ: Telegram → Настройки → Устройства → завершить сеанс.
"""

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alfred.ui import texts as T  # noqa: E402  — названия кнопок меню берём из самого Альфреда

HERE = Path(__file__).resolve().parent
REPORT = HERE / "live_report.txt"
QUIET = 4          # секунд тишины — значит, Альфред договорил
TIMEOUT = 60       # дольше ответа не ждём
PAUSE = 2          # между фразами — чтобы не сработала защита «Не так быстро»


@dataclass
class Step:
    say: str
    expect: list[str]                       # в ответе должно быть хотя бы одно из этих слов (без учёта регистра)
    undo: bool = False                      # фраза что-то записывает — сразу отменяем
    forbid: list[str] = field(default_factory=lambda: ["что-то пошло не так", "не совсем понял"])
    about: str = ""


SCENARIOS = [
    Step("/start", ["🎩"], about="Альфред на связи"),
    Step(T.MENU_TASKS, ["дел"], about="Кнопка «Дела»"),
    Step(T.MENU_NOTES, ["заметк", "заметок"], about="Кнопка «Заметки»"),
    Step(T.MENU_SCHEDULE, ["🗓", "распоряд", "ничего", "свобод"], about="Кнопка «Распорядок»"),
    Step(T.MENU_FINANCE, ["₽", "финанс", "расход", "доход"], about="Кнопка «Финансы»"),
    Step(T.MENU_WEATHER, ["°"], about="Кнопка «Погода»"),
    Step("Купить молоко для робота-проверки завтра", ["молоко"], undo=True,
         about="Дело с датой (GigaChat)"),
    Step("Запиши заметку: робот-проверка, код 4711", ["4711"], undo=True, about="Заметка (GigaChat)"),
    Step("Потратил 350 рублей на такси", ["350"], undo=True, about="Расход (GigaChat)"),
    Step("Иван Роботов должен мне 1200", ["1 200", "1200"], undo=True, about="Долг (GigaChat)"),
    Step("В пятницу в 18:00 тренировка", ["18:00"], undo=True, about="Событие в распорядке (GigaChat)"),
    Step("У Ивана Роботова день рождения 12 марта", ["12 марта"], undo=True, about="День рождения (GigaChat)"),
    Step("Сколько 100 долларов в рублях?", ["₽"], about="Курс валют"),
    Step("Погода на завтра", ["°"], about="Погода обычной речью"),
    Step("Сколько сейчас времени в Токио?", ["токио"], about="Время в городе"),
    Step("Что ты умеешь?", ["пишите мне", "умею", "могу"], about="Справка"),
    Step("Отмена", ["отменять нечего", "отменил", "ничего не жду"], about="«Отмена» без вопроса"),
]


def load_env() -> dict:
    path = HERE / "live_check.env"
    if not path.exists():
        sys.exit("Нет файла tools/live_check.env — скопируйте tools/live_check.env.example и заполните.")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("API_ID", "API_HASH", "BOT_USERNAME"):
        if not env.get(k) or env[k].startswith("…"):
            sys.exit(f"В tools/live_check.env не заполнено: {k}")
    return env


def session_path() -> Path:
    """Ключ входа в Telegram храним вне папки проекта: она лежит в OneDrive и копируется в облако."""
    base = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share") / "AlfredLiveCheck"
    base.mkdir(parents=True, exist_ok=True)
    return base / "live_check"


async def ask(client, bot, text: str, ids: list[int]) -> str:
    """Отправить фразу и собрать все ответы Альфреда (он может ответить несколькими сообщениями)."""
    sent = await client.send_message(bot, text)
    ids.append(sent.id)
    start = last_new = time.monotonic()
    seen: dict[int, str] = {}
    while time.monotonic() - start < TIMEOUT:
        await asyncio.sleep(1)
        replies = await client.get_messages(bot, min_id=sent.id, limit=20)
        fresh = {m.id: m.raw_text or "" for m in replies if not m.out}
        if set(fresh) - set(seen):
            last_new = time.monotonic()
        seen.update(fresh)
        if seen and time.monotonic() - last_new >= QUIET:
            break
    ids.extend(seen)
    return "\n".join(seen[i] for i in sorted(seen))


def verdict(step: Step, answer: str) -> Optional[str]:
    """None — всё хорошо, иначе — что не так."""
    low = answer.lower()
    if not answer:
        return "Альфред не ответил за минуту"
    bad = [w for w in step.forbid if w.lower() in low]
    if bad:
        return f"в ответе «{bad[0]}»"
    if not any(w.lower() in low for w in step.expect):
        return "нет ожидаемых слов: " + " / ".join(step.expect)
    return None


async def main() -> int:
    env = load_env()
    try:
        from telethon import TelegramClient
    except ImportError:
        sys.exit("Нет библиотеки Telethon. Выполните: pip install -r tools/requirements-live.txt")

    client = TelegramClient(str(session_path()), int(env["API_ID"]), env["API_HASH"])
    await client.start()                        # первый раз спросит телефон и код — вводите сами
    bot = await client.get_entity(env["BOT_USERNAME"].lstrip("@"))
    ids: list[int] = []
    results = []
    print(f"\n🤖 Проверяю @{env['BOT_USERNAME'].lstrip('@')}: {len(SCENARIOS)} шагов, примерно 2–3 минуты…\n")
    try:
        for n, step in enumerate(SCENARIOS, 1):
            answer = await ask(client, bot, step.say, ids)
            problem = verdict(step, answer)
            if step.undo and not problem:
                undo = await ask(client, bot, "Отмени последнее", ids)
                if "отменена" not in undo.lower():
                    problem = "не получилось отменить запись — удалите её вручную"
            mark = "✅" if problem is None else "❌"
            line = f"{mark} {n:>2}. {step.about or step.say}"
            if problem:
                short = " ".join(answer.split())[:160]
                line += f"\n      фраза: «{step.say}»\n      что не так: {problem}\n      ответ: {short or '—'}"
            print(line)
            results.append(line)
            await asyncio.sleep(PAUSE)
    finally:
        if env.get("CLEAN_CHAT", "1") == "1" and ids:
            await client.delete_messages(bot, ids)  # убираем переписку робота из чата
        await client.disconnect()

    ok = sum(r.startswith("✅") for r in results)
    total = f"\nИтого: ✅ {ok} из {len(SCENARIOS)}" + ("" if ok == len(SCENARIOS) else f", ❌ {len(SCENARIOS) - ok}")
    print(total)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    REPORT.write_text(f"Проверка {stamp}\n\n" + "\n".join(results) + "\n" + total + "\n", encoding="utf-8")
    print(f"Отчёт сохранён: {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(SCENARIOS) else 1


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")      # чтобы эмодзи печатались в окне Windows
    sys.exit(asyncio.run(main()))
