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
from ..brain.dates import find_dates
from ..brain.parser import BrainResult
from ..database.repositories import Context, ContextRepository, Note, Task
from ..services import search
from ..services.notes import NoteService
from ..services.schedule import ScheduleService
from ..services.tasks import DuplicateError, TaskService, VerificationError
from ..ui import schedule_texts as S
from ..ui import texts as T
from .reply import Reply
from ..database.finance_repo import DebtRepository, OperationRepository, PeopleRepository
from ..services.currency import CurrencyService
from ..services.finance import DebtService, FinanceService
from ..ui import finance_texts as F
from .finance import FinanceMixin
from .birthdays import BirthdayMixin
from .dossier import DossierMixin
from .weather import WEATHER_INTENTS, WeatherMixin
from .med import MED_INTENTS, MedMixin
from ..database.med_repo import MedRepository
from ..services.med import MedService
from ..database.repositories import UserRepository
from ..services.weather import WeatherService
from ..database.dossier_repo import DossierRepository
from ..services.dossier import DossierService
from ..database.birthday_repo import BirthdayRepository
from ..services.birthdays import BirthdayService
from .schedule import SCHEDULE_BUTTONS, ScheduleMixin, infer_type

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


class Alfred(ScheduleMixin, FinanceMixin, BirthdayMixin, DossierMixin, WeatherMixin, MedMixin):
    def __init__(self, brain: Brain, tasks: TaskService, notes: NoteService, schedule: ScheduleService,
                 context: ContextRepository, user_id: int, tz: ZoneInfo,
                 clock: Optional[Callable[[], datetime]] = None, *,
                 finance: Optional[FinanceService] = None, debts: Optional[DebtService] = None,
                 currency: Optional[CurrencyService] = None, birthdays: Optional[BirthdayService] = None,
                 weather: Optional[WeatherService] = None):
        self.brain = brain
        self.tasks = tasks
        self.notes = notes
        self.schedule = schedule
        db = schedule.events.db
        self.finance = finance or FinanceService(OperationRepository(db), user_id)
        self.debts = debts or DebtService(DebtRepository(db), PeopleRepository(db), user_id)
        self.currency = currency or CurrencyService()
        self.birthdays = birthdays or BirthdayService(BirthdayRepository(db), user_id)
        self.dossier = DossierService(DossierRepository(db), user_id)
        self.weather = weather or WeatherService()
        self.users = UserRepository(db)
        self.med = MedService(MedRepository(db), user_id)
        self._rates = None
        self.context = context
        self.user_id = user_id
        self.tz = tz
        self._clock = clock
        self._message = ""
        self._raw_text = ""
        # Лимит запросов к ИИ для приглашённых: функция вернёт текст отказа, если лимит исчерпан.
        self.quota: Optional[Callable[[], Optional[str]]] = None

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
        if ctx.entity_type == "finance" and ctx.entity_id:
            op = self.finance.get(ctx.entity_id)
            if op:
                return f"финансовая запись: {F.op_line(op)}"
        if ctx.entity_type == "med_case" and ctx.entity_id:
            c = self.med.case(ctx.entity_id)
            if c:
                return f"медкарта: {c.title}" + (" (болеет сейчас)" if c.ended is None else " (выздоровел)")
        if ctx.entity_type == "person" and ctx.entity_id:
            c = self.dossier.get(ctx.entity_id)
            if c:
                return f"досье: {c.full_name}"
        if ctx.entity_type == "birthday" and ctx.entity_id:
            b = self.birthdays.get(ctx.entity_id)
            if b:
                return f"день рождения: {self._name(b.person_id)} — {b.day} {T.MONTHS_GEN[b.month - 1]}"
        if ctx.entity_type == "debt" and ctx.entity_id:
            d = self.debts.by_id(ctx.entity_id)
            p = self.debts.person(d.person_id) if d else None
            if d and p:
                who = f"{p.full_name} должен пользователю" if d.direction == "owes_me" else f"пользователь должен {p.full_name}"
                return f"долг: {who} {F.money(d.amount)}"
        if ctx.entity_type == "event" and ctx.entity_id:
            e = self.schedule.get(ctx.entity_id)
            if e:
                return f"событие распорядка «{S.label(e)}» {e.date.isoformat()} в {e.start_time}"
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

    def reset_request(self) -> Reply:
        """🧹 Чистый лист — удалить всё сразу, только после подтверждения."""
        self._clear_pending()
        return Reply("🎩 Сэр, начать с чистого листа?\n\n🧹 Будет удалено ВСЁ:\n📋 дела\n🕰️ распорядок дня\n📝 заметки\n"
                     "💰 расходы, доходы и долги\n🎂 дни рождения\n🗂️ досье\n🩺 медкарта (болезни, лекарства, "
                     "аллергии, врачи)\n\nВернуть данные будет невозможно!",
                     buttons=[[("🧹 Да, начать с чистого листа", "confirm:reset_all")],
                              [("↩️ Нет, оставить", "confirm:no")]])

    def open_section(self, button: str) -> Reply:
        self._clear_pending()
        if button == T.MENU_TASKS:
            return self.tasks_view()
        if button == T.MENU_NOTES:
            return self.notes_view()
        if button == T.MENU_SCHEDULE:
            return self.schedule_day_view(self.today())
        if button == T.MENU_FINANCE:
            return self.statistics_view()
        if button == T.MENU_BIRTHDAYS:
            return self.birthdays_view()
        if button == T.MENU_DOSSIER:
            return self.dossier_view()
        if button == T.MENU_MED:
            return self.medcard_view()
        return Reply(T.SECTION_NOT_READY)

    async def handle_text(self, text: str) -> Reply:
        text = text.strip()
        self._message = text
        self._raw_text = text  # ровно то, что написал пользователь, без подстановок ИИ
        if search.normalize(text).strip(" .!") in T.CANCEL_WORDS:
            return Reply(T.CANCELLED if self._clear_pending() else T.NOTHING_TO_CANCEL)
        if re.fullmatch(r"(?:🧹\s*)?чистый лист[.!]*", search.normalize(text).strip()):
            return self.reset_request()
        if text == T.MENU_WEATHER:
            self._clear_pending()
            return await self.weather_view()
        if text in T.MENU_BUTTONS:
            return self.open_section(text)
        renamed = self.try_rename(text)
        if renamed:
            return renamed
        moved = await self.try_move_note(text)
        if moved:
            return moved
        rate = await self.try_quick_rate(text)
        if rate:
            return rate

        ctx = self._ctx()
        pending_question = ctx.data.get("question") if ctx.intent else None
        if self.quota:
            refusal = self.quota()
            if refusal:
                return Reply(refusal)
        try:
            result = await self.brain.analyze(
                text=text,
                today=self.today(),
                last_object=self._last_description(ctx),
                pending=pending_question,
                task_titles=[t.title for t in self.tasks.active()],
                note_titles=[T.short(n.content, 120) for n in self.notes.all()],
            )
        except BrainUnavailable:
            return Reply(T.AI_UNAVAILABLE)

        result = self._guard_restore(result, text)
        result = self._guard_event_vs_note(result, text)
        result = self._guard_short_answer(result, text, ctx)
        result = self._guard_finance(result, text)
        result = self._guard_birthday(result, text)
        result = self._guard_dossier(result, text)
        result = self._guard_med(result, text)
        if result.intent in WEATHER_INTENTS:
            return await self._weather_intent(result)
        await self._prefetch_rates(result)
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

    def _guard_event_vs_note(self, r: BrainResult, text: str) -> BrainResult:
        """«К врачу паспорт уже не нужен» — про событие, а не про заметку (если слово «заметка» не звучит)."""
        if r.intent not in ("UPDATE_NOTE", "DELETE_NOTE") or "заметк" in text.casefold():
            return r
        etype = infer_type(text)
        if not etype or not self.schedule.find(self.today(), None, etype, None, None):
            return r
        log.info("Guard: %s -> event (%s)", r.intent, etype)
        if r.intent == "DELETE_NOTE":
            return replace(r, intent="DELETE_EVENT", event_type=etype, target=None)
        remove = r.replace_from if not r.replace_to else None
        return replace(r, intent="UPDATE_EVENT", event_type=etype, target=None,
                       comment_remove=r.comment_remove or remove)

    # ------------------------------------------------------------------ выполнение
    def _execute(self, r: BrainResult, ctx: Context) -> Reply:
        if r.intent == "ANSWER":
            if ctx.intent and ctx.missing_parameter and r.answer:
                pending = self._load(ctx.data["result"])
                pending = replace(pending, **{ctx.missing_parameter: r.answer})
                # для медкарты добавляем исходные слова: «мелатонин на ночь» + ответ «Бессонница»
                said = ctx.data.get("said") if pending.intent in MED_INTENTS else None
                self._message = f"{said} {self._message}" if said else f"{self._message} {r.answer}"
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
            "CREATE_EVENT": self._create_event,
            "UPDATE_EVENT": self._update_event,
            "DELETE_EVENT": self._delete_event,
            "SHOW_SCHEDULE": self._show_schedule,
            "CREATE_EXPENSE": self._create_op,
            "CREATE_INCOME": self._create_op,
            "UPDATE_FINANCE": self._update_finance,
            "DELETE_FINANCE": self._delete_finance,
            "SEARCH_FINANCE": self._search_finance,
            "SHOW_STATISTICS": self._show_statistics,
            "SHOW_BALANCE": self._show_balance,
            "CREATE_DEBT": self._create_debt,
            "UPDATE_DEBT": self._update_debt,
            "REPAY_DEBT": self._repay_debt,
            "DELETE_DEBT": self._delete_debt,
            "SHOW_DEBTS": self._show_debts,
            "SHOW_CURRENCY_RATES": self._show_rates,
            "CONVERT_CURRENCY": self._convert,
            "CANCEL": lambda _r: Reply(T.CANCELLED if ctx.intent else T.NOTHING_TO_CANCEL),
            "GREETING": lambda _r: Reply(T.greeting(self.now())),
            "THANKS": lambda _r: Reply(T.pick(*T.THANKS)),
            "OTHER_SECTION": lambda r: self.birthdays_view() if r.section == "birthdays"
            else self.dossier_view() if r.section == "dossier" else Reply(T.SECTION_NOT_READY),
            "CREATE_PERSON": self._create_person,
            "UPDATE_PERSON": self._update_person,
            "DELETE_PERSON": self._delete_person,
            "SHOW_PERSON": self._show_person,
            "SEARCH_PEOPLE": self._search_people,
            "MED_CASE": self._med_case,
            "MED_DRUG": self._med_drug,
            "MED_RECOVER": self._med_recover,
            "MED_SHOW": self._med_show,
            "MED_DELETE": self._med_delete,
            "MED_ALLERGY": self._med_allergy,
            "MED_CONTACT": self._med_contact,
            "CREATE_BIRTHDAY": self._create_birthday,
            "UPDATE_BIRTHDAY": self._update_birthday,
            "DELETE_BIRTHDAY": self._delete_birthday,
            "SHOW_BIRTHDAYS": self._show_birthdays,
        }
        handler = handlers.get(r.intent)
        return handler(r) if handler else Reply(T.NOT_UNDERSTOOD)

    def _ask(self, r: BrainResult, missing: str, question: str) -> Reply:
        # «said» — исходные слова: когда придёт ответ, в них могут быть детали (дозировка, время)
        self._set_pending(r.intent, missing, {"result": self._dump(r), "question": question,
                                              "said": self._message})
        return Reply(question)

    def _ambiguous(self, r: BrainResult, kind: str, items: list) -> Reply:
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка",
                                           "choose": kind})
        if kind == "task":
            text = "🎩 Сэр, уточните, пожалуйста, какое именно дело?"
            rows = [[("⭕ " + T.short(t.title) + T.due_label(t.due_date, self.today()), f"pick:{t.id}")]
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
        due = r.due_date
        # Дату ставим, только если пользователь сам её назвал («завтра», «в пятницу»).
        # Если ИИ придумал «сегодня» от себя — дело записывается без даты.
        if due and self._message and not find_dates(self._message, self.today()):
            log.info("Guard: task date %s not in message, dropped", due)
            due = None
        try:
            task = self.tasks.create(r.title, due, force=r.force_duplicate)
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
        """Компактный список: каждое дело — кнопка. ⭕ — нажми, чтобы отметить; 🟢 — нажми, чтобы вернуть."""
        today = self.today()
        active = self.tasks.active()
        done = [t for t in self.tasks.done_today(today) if t.completed]
        if not active and not done:
            return Reply("🎩 Список дел пуст, Сэр! Можно выдохнуть!", edit=edit, toast=toast)
        rows = [[("⭕ " + T.short(t.title, 34) + T.due_label(t.due_date, today).replace(" — ", " · "),
                  f"task:done:{t.id}")] for t in active[:20]]
        rows += [[("🟢 " + T.short(t.title, 34), f"task:undo:{t.id}")] for t in done[:10]]
        rows.append([("📋 Посмотреть список", "task:list")])
        text = "🎩 Ваши дела, Сэр!" if active else "🎩 Все дела сделаны, Сэр! Великолепно!"
        return Reply(text, buttons=rows, edit=edit, toast=toast)

    def tasks_list_view(self, edit: bool = True) -> Reply:
        """Список для просмотра: что осталось и что уже сделано сегодня."""
        today = self.today()
        active = self.tasks.active()
        done = [t for t in self.tasks.done_today(today) if t.completed]
        parts = []
        if active:
            parts.append("⭕ Не сделано:\n" + "\n".join(T.task_line(t, today)[2:] for t in active))
        if done:
            parts.append("🟢 Сделано сегодня:\n" + "\n".join(T.short(t.title, 60) for t in done))
        body = "\n\n".join(parts) or "Дел нет!"
        return Reply(f"🎩 Ваш список дел, Сэр!\n\n{body}", buttons=[[("↩️ К отметкам", "task:open")]], edit=edit)

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
        rewrite = re.search(r"(?:напиши|запиши|пусть будет|замени на|теперь там)\s*:\s*(.+)$", self._message or "",
                            re.IGNORECASE | re.DOTALL)
        if rewrite:
            # «В заметке про ящик напиши: паспорт в нижнем ящике» — новый текст целиком.
            updated = self.notes.set_content(note, rewrite.group(1).strip())
        elif r.replace_from:
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
        tail = f"\n\nПоказаны последние 15 из {len(notes)}!" if len(notes) > 15 else ""
        return Reply(f"🎩 Ваши заметки, Сэр!\n\n{lines}{tail}")

    # ------------------------------------------------------------------ проверки 10:00 / 14:00 / 18:00
    def check_message(self, period: str, weather: Optional[str] = None) -> Optional[Reply]:
        today = self.today()
        relevant = self.tasks.relevant(today)
        if period == "morning" and weather and (not relevant or self.schedule.morning_merged(today)):
            # дел нет (или список уже пришёл с напоминанием о лекции) — присылаем только погоду
            head = "Погода на сегодня" if self.schedule.morning_merged(today) else T.day_greeting(self.now())
            return Reply(f"🎩 {head}, Сэр!\n\n{weather}")
        if not relevant:
            return None  # нечего напоминать — не беспокоим
        open_lines = "\n".join(T.task_line(t, today) for t in relevant)

        if period == "morning":
            if self.schedule.morning_merged(today):
                return None  # сводка уже пришла вместе с напоминанием о лекции
            wx = f"{weather}\n\n" if weather else ""
            text = f"🎩 {T.day_greeting(self.now())}, Сэр!\n\n{wx}Вот актуальный список дел!\n\n{open_lines}"
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
            [("🟢 Вычеркни все, Альфред, благодарю!", f"chk:all:{period}")],
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

        if data == "task:list":
            return self.tasks_list_view()
        if data == "task:open":
            return self.tasks_view(edit=True)
        if kind == "task" and len(parts) == 3 and parts[2].isdigit() and parts[1] == "undo":
            task = self.tasks.get(int(parts[2]))
            if task and task.completed:
                self.tasks.restore(task)
            return self.tasks_view(edit=True, toast="Вернул в список ⭕")
        if kind == "task" and len(parts) == 3 and parts[2].isdigit():
            task = self.tasks.get(int(parts[2]))
            if parts[1] == "done":
                if task and not task.completed:
                    self.tasks.complete([task], self.now())
                return self.tasks_view(edit=True, toast="Выполнено 🟢")
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
            if ctx.data["choose"] == "finance":
                op = self.finance.get(obj_id)
                if not op:
                    return Reply("🎩 Сэр, этой записи уже нет!", edit=True)
                action = {"UPDATE_FINANCE": self._update_finance, "DELETE_FINANCE": self._delete_finance}[r.intent]
                return replace(action(r, chosen=op), edit=True)
            if ctx.data["choose"] == "debt":
                debt = self.debts.by_id(obj_id)
                if not debt:
                    return Reply("🎩 Сэр, этого долга уже нет!", edit=True)
                action = {"UPDATE_DEBT": self._update_debt, "DELETE_DEBT": self._delete_debt}.get(r.intent)
                if not action:
                    return Reply(T.CANCELLED, edit=True)
                return replace(action(r, chosen=debt), edit=True)
            if ctx.data["choose"] == "person":
                person = self.debts.person(obj_id)
                if not person:
                    return Reply("🎩 Сэр, этого человека уже нет!", edit=True)
                action = {"CREATE_DEBT": self._create_debt, "REPAY_DEBT": self._repay_debt,
                          "DELETE_DEBT": self._delete_debt, "UPDATE_DEBT": None,
                          "CREATE_BIRTHDAY": self._create_birthday, "UPDATE_BIRTHDAY": self._update_birthday,
                          "DELETE_BIRTHDAY": self._delete_birthday,
                          "SHOW_BIRTHDAYS": self._show_birthdays,
                          "CREATE_PERSON": self._create_person, "UPDATE_PERSON": self._update_person,
                          "DELETE_PERSON": self._delete_person, "SHOW_PERSON": self._show_person}.get(r.intent)
                if not action:
                    return Reply(T.CANCELLED, edit=True)
                return replace(action(r, chosen=person), edit=True)
            if ctx.data["choose"] == "med_case":
                case = self.med.case(obj_id)
                if not case:
                    return Reply("🎩 Сэр, этой записи уже нет!", edit=True)
                action = {"MED_DRUG": self._med_drug, "MED_RECOVER": self._med_recover,
                          "MED_SHOW": self._med_show, "MED_DELETE": self._med_delete}.get(r.intent)
                if not action:
                    return Reply(T.CANCELLED, edit=True)
                return replace(action(r, chosen=case), edit=True)
            if ctx.data["choose"] == "birthday":
                b = self.birthdays.get(obj_id)
                if not b:
                    return Reply("🎩 Сэр, этого дня рождения уже нет!", edit=True)
                action = {"UPDATE_BIRTHDAY": self._update_birthday,
                          "DELETE_BIRTHDAY": self._delete_birthday}.get(r.intent)
                if not action:
                    return Reply(T.CANCELLED, edit=True)
                return replace(action(r, chosen=b), edit=True)
            if ctx.data["choose"] == "event":
                event = self.schedule.get(obj_id)
                if not event:
                    return Reply("🎩 Сэр, этого события уже нет в распорядке!", edit=True)
                action = {"UPDATE_EVENT": self._update_event, "DELETE_EVENT": self._delete_event}[r.intent]
                return replace(action(r, chosen=event), edit=True)
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

        if kind == "fin":
            return self.finance_callback(parts)

        if kind == "bd":
            return self.birthday_callback(parts)

        if kind == "dos":
            return self.dossier_callback(parts)

        if kind == "med":
            return self.med_callback(parts)

        if kind == "sched":
            if parts[1] == "week":
                return self.schedule_week_view(edit=True)
            if parts[1] == "month":
                return self.schedule_month_view(edit=True)
            d = self.today() + timedelta(days=1 if parts[1] == "tomorrow" else 0)
            return self.schedule_day_view(d, edit=True)

        if kind == "confirm":
            if parts[1] == "del_rules" and len(parts) == 3:
                return self._confirm_delete_rules(parts[2])
            if parts[1] == "del_series" and len(parts) == 4:
                return self._confirm_delete_series(int(parts[2]), parts[3])
            if parts[1] == "del_ops" and len(parts) == 3:
                return self.confirm_delete_ops(parts[2])
            if parts[1] == "reset_all":
                self.schedule.events.db.wipe_user_data(self.user_id)
                left = (self.tasks.active() or self.notes.all() or
                        self.schedule.between(date(2000, 1, 1), date(2100, 1, 1)) or
                        self.finance.latest(None, 1) or self.debts.active() or
                        self.birthdays.all(self.today()) or self.dossier.all() or
                        self.med.cases() or self.med.allergies() or self.med.contacts())
                if left:
                    raise VerificationError("wipe failed")
                self._clear_pending()
                return Reply("🎩 Готово, Сэр! Всё очищено, начинаем с чистого листа!", edit=True)
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
