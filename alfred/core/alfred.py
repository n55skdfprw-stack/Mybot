"""Ядро Альфреда: получает текст или нажатие кнопки и возвращает ответ.

Не зависит от Telegram — поэтому его легко проверять тестами.
Порядок работы: понять (мозг) → найти объект → выполнить → проверить → ответить.
"""

import logging
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..brain.brain import Brain, BrainUnavailable
from ..brain.parser import BrainResult
from ..database.repositories import Context, ContextRepository, Note, Task
from ..services import search
from ..services.notes import NoteService
from ..services.tasks import DuplicateError, TaskService, VerificationError
from ..ui import texts as T
from .reply import Reply

log = logging.getLogger(__name__)

PENDING_MINUTES = 10

# Фразы, которые однозначно означают «верни вычеркнутое дело».
RESTORE_RE = re.compile(
    r"^\s*(а\s+)?(верни|верните|вернуть|восстанови)\b"
    r"|\bне\s+(сделал|сделала|купил|купила|выполнил|выполнила|успел|успела)\b"
    r"|\bзря\s+(вычеркнул|вычеркнула|отметил|отметила)\b"
    r"|\bотмени\s+выполнени",
    re.IGNORECASE,
)


class Alfred:
    def __init__(self, brain: Brain, tasks: TaskService, notes: NoteService, context: ContextRepository,
                 user_id: int, tz: ZoneInfo, clock: Optional[Callable[[], datetime]] = None):
        self.brain = brain
        self.tasks = tasks
        self.notes = notes
        self.context = context
        self.user_id = user_id
        self.tz = tz
        self._clock = clock
        self._message = ""

    # ------------------------------------------------------------------ время
    def now(self) -> datetime:
        return self._clock() if self._clock else datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    # ------------------------------------------------------------------ контекст
    def _ctx(self) -> Context:
        ctx = self.context.get(self.user_id)
        if ctx.expires_at and ctx.expires_at < self.now().isoformat():
            ctx.intent = ctx.missing_parameter = None
            ctx.data = {}
            ctx.expires_at = None
        return ctx

    def _set_last(self, kind: str, obj_id: Optional[int]) -> None:
        ctx = self._ctx()
        ctx.entity_type, ctx.entity_id = (kind, obj_id) if obj_id else (None, None)
        self.context.save(self.user_id, ctx)

    def _set_pending(self, intent: str, missing: Optional[str], data: dict) -> None:
        ctx = self._ctx()
        ctx.intent, ctx.missing_parameter, ctx.data = intent, missing, data
        ctx.expires_at = (self.now() + timedelta(minutes=PENDING_MINUTES)).isoformat()
        self.context.save(self.user_id, ctx)

    def _clear_pending(self) -> bool:
        ctx = self._ctx()
        had = bool(ctx.intent)
        ctx.intent = ctx.missing_parameter = ctx.expires_at = None
        ctx.data = {}
        self.context.save(self.user_id, ctx)
        return had

    def _last_description(self, ctx: Context) -> Optional[str]:
        if ctx.entity_type == "task" and ctx.entity_id:
            t = self.tasks.get(ctx.entity_id)
            if t:
                return f"дело «{t.title}» ({t.due_date.isoformat() if t.due_date else 'без даты'})"
        if ctx.entity_type == "note" and ctx.entity_id:
            n = self.notes.get(ctx.entity_id)
            if n:
                return f"заметка «{n.content}»"
        return None

    @staticmethod
    def _dump(r: BrainResult) -> dict:
        d = asdict(r)
        for k in ("due_date", "new_due_date"):
            if d[k]:
                d[k] = d[k].isoformat()
        return d

    @staticmethod
    def _load(d: dict) -> BrainResult:
        d = dict(d)
        for k in ("due_date", "new_due_date"):
            if d.get(k):
                d[k] = date.fromisoformat(d[k])
        return BrainResult(**d)

    # ------------------------------------------------------------------ входные точки
    def start(self) -> Reply:
        self._clear_pending()
        return Reply(T.greeting(self.now()))

    def open_section(self, button: str) -> Reply:
        self._clear_pending()
        if button == T.MENU_TASKS:
            return self.tasks_view()
        if button == T.MENU_NOTES:
            return self.notes_view()
        return Reply(T.SECTION_NOT_READY)

    async def handle_text(self, text: str) -> Reply:
        text = text.strip()
        self._message = text
        if search.normalize(text).strip(" .!") in T.CANCEL_WORDS:
            return Reply(T.CANCELLED if self._clear_pending() else T.NOTHING_TO_CANCEL)
        if text in T.MENU_BUTTONS:
            return self.open_section(text)

        ctx = self._ctx()
        pending_question = ctx.data.get("question") if ctx.intent else None
        try:
            result = await self.brain.analyze(
                text=text,
                today=self.today(),
                last_object=self._last_description(ctx),
                pending=pending_question,
                task_titles=[t.title for t in self.tasks.active()],
                note_titles=[n.title for n in self.notes.all()],
            )
        except BrainUnavailable:
            return Reply(T.AI_UNAVAILABLE)

        result = self._guard_restore(result, text)
        try:
            return self._execute(result, ctx)
        except (VerificationError, sqlite3.Error):
            log.exception("Action failed")
            return Reply(T.DB_ERROR)

    def handle_callback(self, data: str) -> Reply:
        try:
            return self._callback(data)
        except (VerificationError, sqlite3.Error):
            log.exception("Callback failed")
            return Reply(T.DB_ERROR, clear_source_buttons=True)

    def _guard_restore(self, r: BrainResult, text: str) -> BrainResult:
        """Страховка: «Верни молоко» — это возврат, даже если ИИ решил, что это новое дело."""
        if r.intent in ("RESTORE_TASK", "COMPLETE_TASK", "DELETE_TASK", "UPDATE_TASK"):
            return r
        if RESTORE_RE.search(search.normalize(text)) and self.tasks.find_completed(text):
            log.info("Guard: %s -> RESTORE_TASK", r.intent)
            return replace(r, intent="RESTORE_TASK", target=None, title=None)
        return r

    # ------------------------------------------------------------------ выполнение
    def _execute(self, r: BrainResult, ctx: Context) -> Reply:
        if r.intent == "ANSWER":
            if ctx.intent and ctx.missing_parameter and r.answer:
                pending = self._load(ctx.data["result"])
                pending = replace(pending, **{ctx.missing_parameter: r.answer})
                self._clear_pending()
                return self._execute(pending, Context())
            return Reply(T.NOT_UNDERSTOOD)

        # Новая команда вместо ответа на вопрос — старый вопрос забываем.
        if ctx.intent:
            self._clear_pending()

        handlers = {
            "CREATE_TASK": self._create_task,
            "UPDATE_TASK": self._update_task,
            "COMPLETE_TASK": self._complete_task,
            "RESTORE_TASK": self._restore_task,
            "DELETE_TASK": self._delete_task,
            "SHOW_TASKS": lambda _r: self.tasks_view(),
            "CREATE_NOTE": self._create_note,
            "UPDATE_NOTE": self._update_note,
            "DELETE_NOTE": self._delete_note,
            "SEARCH_NOTE": self._search_note,
            "SHOW_NOTES": lambda _r: self.notes_view(),
            "CANCEL": lambda _r: Reply(T.CANCELLED if ctx.intent else T.NOTHING_TO_CANCEL),
            "GREETING": lambda _r: Reply(T.greeting(self.now())),
            "THANKS": lambda _r: Reply(T.pick(*T.THANKS)),
            "OTHER_SECTION": lambda _r: Reply(T.SECTION_NOT_READY),
        }
        handler = handlers.get(r.intent)
        return handler(r) if handler else Reply(T.NOT_UNDERSTOOD)

    def _ask(self, r: BrainResult, missing: str, question: str) -> Reply:
        self._set_pending(r.intent, missing, {"result": self._dump(r), "question": question})
        return Reply(question)

    def _ambiguous(self, r: BrainResult, kind: str, items: list) -> Reply:
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка",
                                           "choose": kind})
        if kind == "task":
            text = "🎩 Сэр, уточните, пожалуйста, какое именно дело?"
            rows = [[("☐ " + T.short(t.title) + T.due_label(t.due_date, self.today()), f"pick:{t.id}")]
                    for t in items[:8]]
        else:
            text = "🎩 Сэр, уточните, пожалуйста, какую именно заметку?"
            rows = [[("📝 " + T.short(n.content), f"pick:{n.id}")] for n in items[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply(text, buttons=rows)

    # ------------------------------------------------------------------ дела
    def _resolve(self, r: BrainResult, kind: str) -> list:
        """Находит объект, о котором говорит пользователь. Никогда не выбирает наугад.

        1) название, которое выделил ИИ; 2) слова из самого сообщения;
        3) «последний объект разговора» — только если в сообщении нет названия.
        """
        find = self.tasks.find if kind == "task" else self.notes.find
        get = self.tasks.get if kind == "task" else self.notes.get

        if r.target and r.target != "LAST":
            found = find(r.target)
            if found:
                return found

        by_message = find(self._message) if self._message else []

        ctx = self._ctx()
        last = None
        if ctx.entity_type == kind and ctx.entity_id:
            last = get(ctx.entity_id)
            if kind == "task" and last and last.completed:
                last = None

        if last and (not by_message or any(o.id == last.id for o in by_message)):
            if r.target == "LAST" or not r.target or any(o.id == last.id for o in by_message):
                return [last]
        return by_message

    def _resolve_tasks(self, r: BrainResult) -> list[Task]:
        return self._resolve(r, "task")

    def _create_task(self, r: BrainResult) -> Reply:
        if not r.title:
            return self._ask(r, "title", "🎩 Разумеется, Сэр! Что нужно сделать?")
        try:
            task = self.tasks.create(r.title, r.due_date, force=r.force_duplicate)
        except DuplicateError:
            return Reply("🎩 Сэр, такое дело уже есть в списке!")
        self._set_last("task", task.id)
        head = T.pick("🎩 Записал, Сэр!", "🎩 Разумеется, Сэр! Записал дело!", "🎩 Как прикажете, Сэр! Дело в списке!")
        return Reply(f"{head}\n\n{T.task_line(task, self.today())}")

    def _update_task(self, r: BrainResult, chosen: Optional[Task] = None) -> Reply:
        found = [chosen] if chosen else self._resolve_tasks(r)
        if not found:
            return Reply("🎩 Сэр, я не нашёл такое дело! Уточните, пожалуйста, какое именно нужно изменить?")
        if len(found) > 1:
            return self._ambiguous(r, "task", found)
        task = found[0]
        new_due = r.new_due_date or r.due_date
        if not (r.new_title or new_due or r.clear_due_date):
            self._set_last("task", task.id)
            return Reply(f"🎩 Сэр, что именно изменить в деле «{task.title}»?")
        updated = self.tasks.update(task, r.new_title, new_due, r.clear_due_date)
        self._set_last("task", updated.id)
        lines = []
        if r.new_title and updated.title != task.title:
            lines.append(f"🎩 Готово, Сэр! Теперь дело называется «{updated.title}»!")
        if r.clear_due_date:
            lines.append(f"🎩 Готово, Сэр! Убрал дату у дела «{updated.title}»!")
        elif new_due:
            lines.append(f"🎩 Готово, Сэр! Перенёс «{updated.title}» {T.due_phrase(updated.due_date, self.today())}!")
        return Reply("\n".join(lines) or "🎩 Готово, Сэр!")

    def _complete_task(self, r: BrainResult, chosen: Optional[Task] = None) -> Reply:
        if r.scope == "all" and not chosen:
            active = self.tasks.active()
            if not active:
                return Reply("🎩 Сэр, список дел и так пуст!")
            self.tasks.complete(active, self.now())
            return Reply("🎩 Великолепно, Сэр! Вычеркнул все дела!")
        found = [chosen] if chosen else self._resolve_tasks(r)
        if not found:
            return Reply("🎩 Сэр, я не нашёл такое дело! Уточните, пожалуйста, какое именно выполнено?")
        if len(found) > 1:
            return self._ambiguous(r, "task", found)
        task = found[0]
        self.tasks.complete([task], self.now())
        self._set_last("task", None)
        return Reply(T.pick(f"🎩 Отлично, Сэр! «{task.title}» выполнено!",
                            f"🎩 Великолепно, Сэр! Вычеркнул «{task.title}»!"))

    def _restore_task(self, r: BrainResult, chosen: Optional[Task] = None) -> Reply:
        """Возвращает вычеркнутое дело в список (с прежней датой)."""
        if chosen:
            found = [chosen]
        else:
            found = []
            if r.target and r.target != "LAST":
                found = self.tasks.find_completed(r.target)
            if not found and self._message:
                found = self.tasks.find_completed(self._message)
            if not found:
                found = self.tasks.completed_on(self.today())
        if not found:
            return Reply("🎩 Сэр, я не нашёл такое дело среди вычеркнутых! Уточните, пожалуйста, какое вернуть?")
        if len(found) > 1:
            return self._ambiguous(r, "task", found)
        task = found[0]
        if any(search.normalize(t.title) == search.normalize(task.title) for t in self.tasks.active()):
            return Reply(f"🎩 Сэр, дело «{T.short(task.title)}» уже есть в списке!")
        restored = self.tasks.restore(task)
        self._set_last("task", restored.id)
        return Reply(f"🎩 Вернул, Сэр!\n\n{T.task_line(restored, self.today())}")

    def _delete_task(self, r: BrainResult, chosen: Optional[Task] = None) -> Reply:
        if r.scope == "all" and not chosen:
            if not self.tasks.active():
                return Reply("🎩 Сэр, список дел и так пуст!")
            return Reply("🎩 Сэр, вы действительно хотите удалить все дела?",
                         buttons=[[("🗑 Да, удалить все", "confirm:del_all_tasks"),
                                   ("↩️ Нет, оставить", "confirm:no")]])
        found = [chosen] if chosen else self._resolve_tasks(r)
        if not found:
            return Reply("🎩 Сэр, я не нашёл такое дело! Уточните, пожалуйста, какое именно удалить?")
        if len(found) > 1:
            return self._ambiguous(r, "task", found)
        task = found[0]
        self.tasks.delete([task])
        self._set_last("task", None)
        return Reply(f"🎩 Удалил, Сэр! Дела «{task.title}» больше нет в списке!")

    def tasks_view(self, edit: bool = False, toast: Optional[str] = None) -> Reply:
        active = self.tasks.active()
        today = self.today()
        if not active:
            return Reply("🎩 Список дел пуст, Сэр! Можно выдохнуть!", edit=edit, toast=toast)
        lines = "\n".join(T.task_line(t, today) for t in active)
        text = f"🎩 Ваши дела, Сэр!\n\n{lines}"
        rows = [[("✅ " + T.short(t.title), f"task:done:{t.id}")] for t in active[:20]]
        return Reply(text, buttons=rows, edit=edit, toast=toast)

    # ------------------------------------------------------------------ заметки
    def _resolve_notes(self, r: BrainResult) -> list[Note]:
        return self._resolve(r, "note")

    def _create_note(self, r: BrainResult) -> Reply:
        if not r.content:
            return self._ask(r, "content", "🎩 Разумеется, Сэр! Что записать?")
        note = self.notes.create(r.content)
        self._set_last("note", note.id)
        head = T.pick("🎩 Разумеется, Сэр! Сохранил заметку!", "🎩 Записал, Сэр! Заметка сохранена!")
        return Reply(f"{head}\n\n{T.note_line(note)}")

    def _update_note(self, r: BrainResult, chosen: Optional[Note] = None) -> Reply:
        found = [chosen] if chosen else self._resolve_notes(r)
        if not found:
            return Reply("🎩 Сэр, я не нашёл такую заметку! Уточните, пожалуйста, какую именно нужно изменить?")
        if len(found) > 1:
            return self._ambiguous(r, "note", found)
        note = found[0]
        self._set_last("note", note.id)
        if r.replace_from:
            updated = self.notes.replace_text(note, r.replace_from, r.replace_to or "")
            if updated is None:
                return Reply(f"🎩 Сэр, в заметке нет слов «{r.replace_from}»! Что именно поменять?")
        elif r.content:
            updated = self.notes.set_content(note, r.content)
        else:
            return Reply("🎩 Сэр, что именно изменить в заметке?")
        return Reply(f"🎩 Готово, Сэр! Обновил заметку!\n\n{T.note_line(updated)}")

    def _delete_note(self, r: BrainResult, chosen: Optional[Note] = None) -> Reply:
        if r.scope == "all" and not chosen:
            if not self.notes.all():
                return Reply("🎩 Сэр, заметок и так нет!")
            return Reply("🎩 Сэр, вы действительно хотите удалить все заметки?",
                         buttons=[[("🗑 Да, удалить все", "confirm:del_all_notes"),
                                   ("↩️ Нет, оставить", "confirm:no")]])
        found = [chosen] if chosen else self._resolve_notes(r)
        if not found:
            return Reply("🎩 Сэр, я не нашёл такую заметку! Уточните, пожалуйста, какую именно удалить?")
        if len(found) > 1:
            return self._ambiguous(r, "note", found)
        self.notes.delete(found)
        self._set_last("note", None)
        return Reply("🎩 Удалил заметку, Сэр!")

    def _search_note(self, r: BrainResult) -> Reply:
        query = r.query or r.target
        if not query:
            return self._ask(r, "query", "🎩 Сэр, что поискать в заметках?")
        found = self.notes.find(query)
        if not found:
            return Reply(f"🎩 Сэр, я не нашёл заметок про «{query}»!")
        if len(found) == 1:
            self._set_last("note", found[0].id)
        lines = "\n".join(T.note_line(n) for n in found[:10])
        return Reply(f"🎩 Вот что я нашёл, Сэр!\n\n{lines}")

    def notes_view(self) -> Reply:
        notes = self.notes.all()
        if not notes:
            return Reply("🎩 Заметок пока нет, Сэр! Просто напишите мне, что запомнить!")
        lines = "\n".join(T.note_line(n) for n in notes[:15])
        tail = "\n\nЧтобы найти заметку, просто спросите меня, Сэр!"
        if len(notes) > 15:
            tail = f"\n\nПоказаны последние 15 из {len(notes)}!" + tail
        return Reply(f"🎩 Ваши заметки, Сэр!\n\n{lines}{tail}")

    # ------------------------------------------------------------------ проверки 10:00 / 14:00 / 18:00
    def check_message(self, period: str) -> Optional[Reply]:
        today = self.today()
        relevant = self.tasks.relevant(today)
        if not relevant:
            return None  # нечего напоминать — не беспокоим
        open_lines = "\n".join(T.task_line(t, today) for t in relevant)

        if period == "morning":
            text = f"🎩 {T.day_greeting(self.now())}, Сэр!\n\nВот актуальный список дел!\n\n{open_lines}"
            buttons = [[("❌ Не актуально!", "chk:notactual"), ("👍 Спасибо, Альфред!", "chk:thanks")]]
            return Reply(text, buttons=buttons)

        done = self.tasks.done_today(today)
        done_lines = "\n".join(T.task_line(t, today, done=True) for t in done)
        body = open_lines + (f"\n{done_lines}" if done_lines else "")
        if period == "day":
            head = "🎩 Сэр, позвольте напомнить о том, что требует вашего внимания!"
        else:
            head = "🎩 Сэр, позвольте узнать, всё ли удалось завершить за сегодня!"
        buttons = [
            [("👋 Пока что всё, Альфред!", f"chk:enough:{period}")],
            [("🔥 Весь в делах, Альфред!", f"chk:busy:{period}")],
            [("☑️ Вычеркни все, Альфред, благодарю!", f"chk:all:{period}")],
        ]
        return Reply(f"{head}\n\n{body}", buttons=buttons)

    # ------------------------------------------------------------------ кнопки
    def _drop_view(self, edit: bool) -> Reply:
        relevant = self.tasks.relevant(self.today())
        if not relevant:
            return Reply("🎩 Благодарю, Сэр! Список обновлён!", edit=edit)
        rows = [[("❌ " + T.short(t.title), f"task:drop:{t.id}")] for t in relevant[:20]]
        rows.append([("✅ Готово", "chk:dropdone")])
        return Reply("🎩 Отметьте, какие дела больше не актуальны, Сэр!", buttons=rows, edit=edit)

    def _callback(self, data: str) -> Reply:
        parts = data.split(":")
        kind = parts[0]

        if kind == "task" and len(parts) == 3 and parts[2].isdigit():
            task = self.tasks.get(int(parts[2]))
            if parts[1] == "done":
                if task and not task.completed:
                    self.tasks.complete([task], self.now())
                return self.tasks_view(edit=True, toast="Выполнено ✅")
            if parts[1] == "drop":
                if task:
                    self.tasks.delete([task])
                return self._drop_view(edit=True)

        if kind == "pick":
            ctx = self._ctx()
            if parts[1] == "cancel" or not ctx.intent or "choose" not in ctx.data:
                self._clear_pending()
                return Reply(T.CANCELLED, edit=True)
            r = self._load(ctx.data["result"])
            obj_id = int(parts[1])
            self._clear_pending()
            if ctx.data["choose"] == "task":
                task = self.tasks.get(obj_id)
                if not task:
                    return Reply("🎩 Сэр, этого дела уже нет в списке!", edit=True)
                action = {"UPDATE_TASK": self._update_task, "COMPLETE_TASK": self._complete_task,
                          "RESTORE_TASK": self._restore_task, "DELETE_TASK": self._delete_task}[r.intent]
                return replace(action(r, chosen=task), edit=True)
            note = self.notes.get(obj_id)
            if not note:
                return Reply("🎩 Сэр, этой заметки уже нет!", edit=True)
            action = {"UPDATE_NOTE": self._update_note, "DELETE_NOTE": self._delete_note}[r.intent]
            return replace(action(r, chosen=note), edit=True)

        if kind == "confirm":
            if parts[1] == "del_all_tasks":
                self.tasks.delete(self.tasks.active())
                self._set_last("task", None)
                return Reply("🎩 Готово, Сэр! Все дела удалены!", edit=True)
            if parts[1] == "del_all_notes":
                self.notes.delete(self.notes.all())
                self._set_last("note", None)
                return Reply("🎩 Готово, Сэр! Все заметки удалены!", edit=True)
            return Reply("🎩 Как прикажете, Сэр! Ничего не удалял!", edit=True)

        if kind == "chk":
            action = parts[1]
            period = parts[2] if len(parts) > 2 else "day"
            evening = period == "evening"
            if action == "thanks":
                return Reply(T.pick(*T.THANKS), clear_source_buttons=True)
            if action == "notactual":
                return replace(self._drop_view(edit=False), clear_source_buttons=True)
            if action == "dropdone":
                return Reply("🎩 Благодарю, Сэр! Список обновлён!", edit=True)
            if action == "enough":
                return Reply("🎩 Благодарю, Сэр! Хорошего вечера!" if evening else "🎩 Благодарю, Сэр! Не отвлекаю!",
                             clear_source_buttons=True)
            if action == "busy":
                return Reply("🎩 Умолкаю, Сэр!", clear_source_buttons=True)
            if action == "all":
                self.tasks.complete(self.tasks.relevant(self.today()), self.now())
                return Reply("🎩 Великолепно, Сэр! Хорошего вечера!" if evening
                             else "🎩 Великолепно, Сэр! Больше не докучаю!", clear_source_buttons=True)

        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)
