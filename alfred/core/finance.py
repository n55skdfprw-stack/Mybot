"""Финансы в ядре Альфреда. Подключается к классу Alfred как дополнение (mixin).

Суммы, валюты и периоды разбирает код (brain/money.py), а не ИИ.
Долги — отдельная сущность: не доход и не расход, на остаток не влияют.
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from ..brain.dates import MONTHS, _norm, parse_date
from ..brain.money import VAGUE, parse_amount, parse_conversion, parse_currency, parse_period
from ..brain.parser import BrainResult
from ..database.finance_repo import Debt, Operation, Person
from ..services import search
from ..services.currency import CurrencyService, Rates
from ..services.finance import DebtService, FinanceService
from ..ui import finance_texts as F
from ..ui import texts as T
from ..ui.texts import MONTHS_GEN
from .reply import Reply

log = logging.getLogger(__name__)

FIN_BUTTONS = [
    [("💸 Расходы", "fin:exp"), ("💵 Доходы", "fin:inc")],
    [("📊 Статистика", "fin:stats"), ("🤝 Долги", "fin:debts")],
    [("💱 Курсы валют", "fin:rates"), ("🔎 Поиск", "fin:search")],
    [("✏️ Изменение/удаление", "fin:edit")],
]
RATE_INTENTS = {"SHOW_CURRENCY_RATES", "CONVERT_CURRENCY"}
OP_INTENTS = {"CREATE_EXPENSE", "CREATE_INCOME", "UPDATE_FINANCE"}
FLAGS = {"USD": "🇺🇸 Доллар", "EUR": "🇪🇺 Евро", "CNY": "🇨🇳 Юань", "GBP": "🇬🇧 Фунт", "TRY": "🇹🇷 Лира",
         "KZT": "🇰🇿 Тенге", "BYN": "🇧🇾 Белорусский рубль", "AED": "🇦🇪 Дирхам", "JPY": "🇯🇵 Иена",
         "CHF": "🇨🇭 Франк", "AMD": "🇦🇲 Армянский драм", "KRW": "🇰🇷 Вона", "PLN": "🇵🇱 Злотый",
         "THB": "🇹🇭 Бат", "INR": "🇮🇳 Индийская рупия", "IDR": "🇮🇩 Индонезийская рупия", "GEL": "🇬🇪 Лари",
         "KGS": "🇰🇬 Сом", "TJS": "🇹🇯 Сомони", "UZS": "🇺🇿 Сум", "UAH": "🇺🇦 Гривна", "AZN": "🇦🇿 Манат",
         "TMT": "🇹🇲 Туркменский манат", "MDL": "🇲🇩 Молдавский лей", "RON": "🇷🇴 Румынский лей",
         "BGN": "🇧🇬 Болгарский лев", "HUF": "🇭🇺 Форинт", "CZK": "🇨🇿 Чешская крона", "SEK": "🇸🇪 Шведская крона",
         "NOK": "🇳🇴 Норвежская крона", "DKK": "🇩🇰 Датская крона", "HKD": "🇭🇰 Гонконгский доллар",
         "SGD": "🇸🇬 Сингапурский доллар", "CAD": "🇨🇦 Канадский доллар", "AUD": "🇦🇺 Австралийский доллар",
         "NZD": "🇳🇿 Новозеландский доллар", "BRL": "🇧🇷 Бразильский реал", "ZAR": "🇿🇦 Рэнд",
         "EGP": "🇪🇬 Египетский фунт", "QAR": "🇶🇦 Катарский риал", "VND": "🇻🇳 Донг", "RSD": "🇷🇸 Сербский динар"}
RATE_WORDS = re.compile(r"\b(курс\w*|какой|какая|сколько|стоит|сейчас|сегодня|цб|по|у|на|к|рублю|рублям)\b")
PURCHASE_RE = re.compile(r"\b(потратил\w*|купил\w*|заплатил\w*|оплатил\w*|отдал\w*\s+за)\b", re.IGNORECASE)
TOPUP_RE = re.compile(r"\bпополн\w*", re.IGNORECASE)
INCOME_RE = re.compile(r"\b(зп|з/п|зарплат\w*|аванс\w*|преми\w*|стипенди\w*|пенси\w*|кэшбэк\w*|кешбэк\w*|"
                       r"получил\w*|пришл\w*|пришёл|пришел|заработал\w*|вернули|возврат\w*|доход\w*)\b", re.IGNORECASE)
# На эти вопросы Альфреда короткий ответ — это всегда ответ, а не новая команда.
SHORT_ANSWER_PARAMS = {"category", "person", "amount_text", "new_amount_text", "bday_text", "query"}


def _day_short(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def direction_from_text(text: Optional[str]) -> Optional[str]:
    """Кто кому должен, по словам пользователя."""
    if not text:
        return None
    t = _norm(text)
    if re.search(r"\bя\s+(должен|должна|задолжал\w*|занял\w*\s+у|взял\w*\s+в\s+долг|верн\w+|отдал\w*)|"
                 r"\bкому\s+я\b|\bмой\s+долг|\bя\b\s*$", t):
        return "i_owe"
    if re.search(r"должен\s+мне|должна\s+мне|мне\s+должн|верн\w+\s+мне|отдал\w*\s+мне|"
                 r"дал\w*\s+в\s+долг|одолжил\w*|занял\w*\s+(?!у\b)|\bмне\b|\bон\b|\bона\b", t):
        return "owes_me"
    return None


class FinanceMixin:
    finance: FinanceService
    debts: DebtService
    currency: CurrencyService
    _rates: Optional[Rates] = None

    # ------------------------------------------------------------ подготовка (асинхронная часть)
    async def _prefetch_rates(self, r: BrainResult) -> None:
        needs = r.intent in RATE_INTENTS
        if r.intent in OP_INTENTS:
            cur = (parse_currency(r.currency) or parse_currency(r.amount_text) or
                   parse_currency(r.new_amount_text) or parse_currency(self._message))
            needs = bool(cur and cur != "RUB")
        if needs:
            self._rates = await self.currency.get()

    async def handle_callback_async(self, data: str) -> Reply:
        if data.startswith("wx:"):
            return await self.weather_callback(data)
        if data.startswith("bd:gift:") and data.split(":")[2].isdigit():
            return await self.gift_view(int(data.split(":")[2]))
        if data == "fin:rates":
            self._rates = await self.currency.get()
        return self.handle_callback(data)

    def _guard_finance(self, r: BrainResult, text: str) -> BrainResult:
        """«Купил хлеб за 100» — это расход, а не дело «Купить хлеб»."""
        from dataclasses import replace
        if r.intent == "CREATE_TASK" and PURCHASE_RE.search(text) and parse_amount(text):
            return replace(r, intent="CREATE_EXPENSE", amount_text=None, category=None, description=r.title)
        # «Зп 1000», «Пришла премия 5000» — это доход, а не расход.
        if r.intent == "CREATE_EXPENSE" and INCOME_RE.search(text) and not PURCHASE_RE.search(text):
            return replace(r, intent="CREATE_INCOME")
        # «Пополнил БСК на 1000», «Оплатил связь» — это трата, а не доход.
        if r.intent == "CREATE_INCOME" and (
                PURCHASE_RE.search(text) or
                (TOPUP_RE.search(text) and F.match_category(text) in ("Транспорт", "Связь"))):
            return replace(r, intent="CREATE_EXPENSE")
        correction = re.search(r"\bне\s+\d", _norm(text))
        # «Сергей должен мне не 5000, а 6000» — это исправление, а не новый долг.
        if r.intent == "CREATE_DEBT" and correction:
            return replace(r, intent="UPDATE_DEBT")
        # «Не 5000, а 6000» сразу после долга — исправляем долг, а не расход.
        ctx = self._ctx()
        if (r.intent in ("UPDATE_FINANCE", "DELETE_FINANCE") and ctx.entity_type == "debt" and ctx.entity_id
                and r.target in (None, "LAST") and not r.op_type and not r.new_category
                and not F.match_category(text) and not re.search(r"расход|доход|трат", _norm(text))):
            return replace(r, intent="UPDATE_DEBT" if r.intent == "UPDATE_FINANCE" else "DELETE_DEBT")
        return r

    def _guard_short_answer(self, r: BrainResult, text: str, ctx) -> BrainResult:
        """Альфред спросил «На что был расход?», пользователь ответил «Такси» — это ответ, а не поиск."""
        from dataclasses import replace
        if r.intent == "ANSWER" or not ctx.intent or ctx.missing_parameter not in SHORT_ANSWER_PARAMS:
            return r
        words = text.strip(" .!?").split()
        if not words or len(words) > 4 or "?" in text:
            return r
        has_digits = bool(re.search(r"\d", text))
        if ctx.missing_parameter in ("category", "person") and has_digits:
            return r
        if ctx.missing_parameter in ("amount_text", "new_amount_text") and not parse_amount(text):
            return r
        if ctx.missing_parameter == "bday_text":
            from ..services.birthdays import parse_birthday
            if not parse_birthday(text, self.today()):
                return r
        log.info("Guard: %s -> ANSWER (%s)", r.intent, ctx.missing_parameter)
        return replace(r, intent="ANSWER", answer=text.strip(" .!"))

    # ------------------------------------------------------------ помощники
    def _op_day(self, r: BrainResult) -> date:
        t = _norm(r.op_when or self._message or "")
        if re.search(r"\bпозавчера\b", t):
            return self.today() - timedelta(days=2)
        if re.search(r"\bвчера\b", t):
            return self.today() - timedelta(days=1)
        d = parse_date(r.op_when, self.today()) if r.op_when else None
        if d and d > self.today():  # «24 сентября» в прошлом году не бывает будущим расходом
            d = d.replace(year=d.year - 1)
        return d or self.today()

    def _money_in(self, amount_text: Optional[str], currency: Optional[str]) -> tuple | Reply | None:
        """(рубли, исходная сумма или None, валюта или None) — или Reply, если нет курса."""
        # Сумму берём из слов пользователя: ИИ может переписать «120к» как «120».
        amount = parse_amount(self._message)
        if amount is None and amount_text:
            amount = parse_amount(amount_text)
        if amount is None:
            return None
        cur = (parse_currency(currency) or parse_currency(amount_text) or parse_currency(self._message) or "RUB")
        if cur == "RUB":
            return amount, None, None
        rub = self._rates.to_rub(amount, cur) if self._rates else None
        if rub is None:
            return Reply("🎩 Сэр, не удалось получить курс ЦБ! Запишите, пожалуйста, сумму в рублях!")
        return rub, amount, cur

    # ------------------------------------------------------------ расходы и доходы
    def _said_category(self, r: BrainResult) -> Optional[str]:
        """Категория, только если она правда прозвучала: ИИ любит подставлять её «от себя»."""
        heard = " ".join(filter(None, [self._message, (self._ctx().data or {}).get("answers")]))
        from_words = F.match_category(self._message)
        if from_words:
            return from_words
        for candidate in (r.category, r.description):
            if candidate and search.score(candidate, heard) > 0:
                return F.normalize_category(candidate)
        return None

    def _create_op(self, r: BrainResult) -> Reply:
        op_type = "income" if r.intent == "CREATE_INCOME" else "expense"
        money = self._money_in(r.amount_text, r.currency)
        if isinstance(money, Reply):
            return money
        if money is None:
            return self._ask(r, "amount_text", "🎩 Разумеется, Сэр! Какая сумма?")
        rub, orig, cur = money
        said = self._said_category(r)
        if op_type == "expense":
            category = said
            if not category:
                return self._ask(r, "category", "🎩 Разумеется, Сэр! На что был расход?")
        else:
            category = said
        heard = " ".join(filter(None, [self._message, r.description, r.category]))
        detail = F.match_detail(category, heard)
        op = self.finance.add(op_type, rub, category, detail or r.description, self._op_day(r), orig, cur)
        self._set_last("finance", op.id)
        amount = F.money(op.amount) + (f" ({F.money(orig, cur)})" if cur else "")
        what = f" — {category[:1].upper() + category[1:]}" if category else ""
        kind = "Расход" if op_type == "expense" else "Доход"
        when = "" if op.date == self.today() else f" ({_day_short(op.date)})"
        return Reply(f"🎩 Записал, Сэр!\n{kind}: {amount}{what}{when}!")

    def _resolve_ops(self, r: BrainResult) -> list[Operation]:
        count = r.count
        m = re.search(r"последни[ехм]\s+(\d+|две|два|три|четыре|пять)", _norm(self._message or ""))
        if m and not count:
            count = {"две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5}.get(m.group(1)) or int(m.group(1))
        op_type = r.op_type
        if not op_type:
            t = _norm(self._message or "")
            op_type = "income" if re.search(r"доход", t) else "expense" if re.search(r"расход|трат", t) else None
        if count and count > 1:
            return self.finance.latest(op_type, count)
        if r.target and r.target != "LAST":
            found = self.finance.find(r.target, op_type)
            if found:
                return found
        by_words = self.finance.find(self._message or "", op_type) if not (r.target == "LAST") else []
        if by_words and len(by_words) < 5:
            return by_words
        ctx = self._ctx()
        if ctx.entity_type == "finance" and ctx.entity_id and not op_type:
            op = self.finance.get(ctx.entity_id)
            if op:
                return [op]
        return self.finance.latest(op_type, 1)

    def _narrow_ops(self, found: list[Operation]) -> list[Operation]:
        """Несколько кандидатов: «не 700, а 800» → запись на 700; иначе — последняя упомянутая."""
        if len(found) <= 1:
            return found
        m = re.search(r"\bне\s+(\d[\d\s]*(?:[.,]\d+)?\s*(?:к|k|тыс\w*|тр)?)", _norm(self._message or ""))
        if m:
            old = parse_amount(m.group(1))
            same = [o for o in found if old is not None and abs(o.amount - old) < 0.01]
            if same:
                return same[:1]
        ctx = self._ctx()
        if ctx.entity_type == "finance" and ctx.entity_id:
            last = [o for o in found if o.id == ctx.entity_id]
            if last:
                return last
        return found

    def _ambiguous_ops(self, r: BrainResult, ops: list[Operation]) -> Reply:
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка",
                                           "choose": "finance"})
        rows = [[(F.op_line(o), f"pick:{o.id}")] for o in ops[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply("🎩 Сэр, уточните, пожалуйста, какую именно запись?", buttons=rows)

    def _update_finance(self, r: BrainResult, chosen: Optional[Operation] = None) -> Reply:
        found = [chosen] if chosen else self._narrow_ops(self._resolve_ops(r))
        if not found:
            return Reply("🎩 Сэр, я не нашёл такую запись! Уточните, пожалуйста, какую именно нужно изменить?")
        if len(found) > 1:
            return self._ambiguous_ops(r, found)
        op = found[0]
        amount_source = r.new_amount_text or r.amount_text
        new_money = None
        if amount_source or re.search(r"\bне\s+\d", _norm(self._message or "")) or parse_amount(self._message):
            new_money = self._money_in(amount_source, r.currency)
            if isinstance(new_money, Reply):
                return new_money
        new_category = r.new_category or (r.category if r.category and not new_money else None)
        new_category = F.normalize_category(new_category) if new_category else None
        if new_money and abs(new_money[0] - op.amount) < 0.001 and not new_category:
            new_money = None
        if not new_money and not new_category:
            self._set_last("finance", op.id)
            return Reply("🎩 Сэр, что именно изменить в этой записи?\n\n" + F.op_line(op))
        rub, orig, cur = new_money if new_money else (None, None, None)
        updated = self.finance.update(op, rub, new_category, (orig, cur) if cur else None)
        self._set_last("finance", updated.id)
        return Reply("🎩 Готово, Сэр! Исправил запись!\n\n" + F.op_line(updated))

    def _delete_finance(self, r: BrainResult, chosen: Optional[Operation] = None) -> Reply:
        found = [chosen] if chosen else self._resolve_ops(r)
        if not found:
            return Reply("🎩 Сэр, я не нашёл такую запись! Уточните, пожалуйста, какую именно удалить?")
        several = r.count and r.count > 1 or re.search(r"последни[ехм]\s+(\d+|две|два|три)", _norm(self._message))
        if len(found) > 1 and not several:
            return self._ambiguous_ops(r, found)
        if len(found) > 1:
            lines = "\n".join(F.op_line(o) for o in found)
            ids = ",".join(str(o.id) for o in found)
            return Reply(f"🎩 Сэр, вы действительно хотите удалить эти записи ({len(found)})?\n\n{lines}",
                         buttons=[[("🗑 Да, удалить", f"confirm:del_ops:{ids}"), ("↩️ Нет, оставить", "confirm:no")]])
        self.finance.delete(found)
        self._set_last("finance", None)
        return Reply("🎩 Удалил запись, Сэр!\n\n❌ " + F.op_line(found[0]))

    # ------------------------------------------------------------ поиск, статистика, остаток
    def _period(self, r: BrainResult):
        period = parse_period(r.period_text, self.today()) or parse_period(self._message, self.today())
        return period

    def _search_finance(self, r: BrainResult) -> Reply:
        period = self._period(r)
        if period == VAGUE:
            return self._ask(r, "period_text", "🎩 Сэр, за какой период показать данные?")
        start, end, label = period or (self.today().replace(day=1), self.today(), "за месяц")
        t = _norm(self._message or "")
        op_type = r.op_type or ("income" if re.search(r"заработ|получил|доход|пришл", t) else "expense")
        category = (F.normalize_category(r.category) if r.category else None) or F.match_category(self._message)
        ops = self.finance.between(start, end, op_type)
        if category:
            ops = [o for o in ops if o.category == category or
                   (o.description and category.casefold() in o.description.casefold())]
        total = sum(o.amount for o in ops)
        kind = "доходов" if op_type == "income" else "расходов"
        if not ops:
            about = f" на {category.lower()}" if category and op_type == "expense" else ""
            return Reply(f"🎩 Сэр, {label} {kind}{about} не было!")
        lines = "\n".join(F.op_line(o) for o in ops[:10])
        more = f"\n…и ещё записей: {len(ops) - 10}" if len(ops) > 10 else ""
        if category and op_type == "expense":
            head = f"🎩 На {category.lower()} {label} — {F.money(total)}, Сэр!"
        else:
            head = f"🎩 {'Доходы' if op_type == 'income' else 'Расходы'} {label} — {F.money(total)}, Сэр!"
        return Reply(f"{head}\n\n{lines}{more}")

    def statistics_view(self, period=None, edit: bool = False) -> Reply:
        start, end, label = period or (self.today().replace(day=1), self.today(), "за месяц")
        ops = self.finance.between(start, end)
        income = sum(o.amount for o in ops if o.type == "income")
        expense = sum(o.amount for o in ops if o.type == "expense")
        by_cat: dict[str, float] = {}
        for o in ops:
            if o.type == "expense":
                by_cat[o.category or "Прочее"] = by_cat.get(o.category or "Прочее", 0) + o.amount
        cats = "\n".join(f"{F.emoji(c)} {c} — {F.money(v)}" for c, v in sorted(by_cat.items(), key=lambda x: -x[1]))
        text = (f"🎩 Ваши финансы, Сэр!\n\n💰 Остаток: {F.money(self.finance.balance())}\n\n"
                f"📈 Доходы {label}: {F.money(income)}\n📉 Расходы {label}: {F.money(expense)}")
        if cats:
            text += "\n\n" + cats
        return Reply(text, buttons=FIN_BUTTONS, edit=edit)

    def _show_statistics(self, r: BrainResult) -> Reply:
        period = self._period(r)
        if period == VAGUE:
            return self._ask(r, "period_text", "🎩 Сэр, за какой период показать данные?")
        return self.statistics_view(period)

    def _show_balance(self, r: BrainResult) -> Reply:
        return Reply(f"🎩 Ваш остаток, Сэр: {F.money(self.finance.balance())}!")

    # ------------------------------------------------------------ группы записей
    @staticmethod
    def _group_key(o: Operation) -> tuple:
        return (o.date, o.type, o.category or ("Доход" if o.type == "income" else "Прочее"))

    def _groups(self, op_type: str, limit: int = 60) -> list[list[Operation]]:
        """Записи одного дня и одной категории — в одну группу (две поездки на автобусе = одна строка)."""
        groups: dict[tuple, list[Operation]] = {}
        for o in self.finance.latest(op_type, limit):
            groups.setdefault(self._group_key(o), []).append(o)
        return list(groups.values())

    def _op_time(self, o: Operation) -> str:
        try:
            dt = datetime.fromisoformat(o.created_at)
            return dt.astimezone(self.tz).strftime("%H:%M")
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def group_line(ops: list[Operation]) -> str:
        o = ops[0]
        total = sum(x.amount for x in ops)
        day = f"{o.date.day} {F.MONTHS_GEN[o.date.month - 1][:3]}"
        sign = "−" if o.type == "expense" else "+"
        what = o.category or o.description or ("Доход" if o.type == "income" else "Расход")
        icon = F.emoji(o.category) if o.type == "expense" else "💵"
        count = f" ×{len(ops)}" if len(ops) > 1 else ""
        return f"{icon} {day} · {what} · {sign}{F.money(total)}{count}"

    def ops_list_view(self, op_type: str, edit: bool = False) -> Reply:
        groups = self._groups(op_type)
        kind = "расходы" if op_type == "expense" else "доходы"
        if not groups:
            return Reply(f"🎩 Сэр, {kind} пока не записаны!", buttons=FIN_BUTTONS, edit=edit)
        rows = [[(self.group_line(g), f"fin:grp:{g[0].id}")] for g in groups[:12]]
        rows.append([("↩️ Назад", "fin:stats")])
        return Reply(f"🎩 Последние {kind}, Сэр!", buttons=rows, edit=edit)

    def group_view(self, op_id: int, edit: bool = True) -> Reply:
        op = self.finance.get(op_id)
        if not op:
            return self.ops_list_view("expense", edit=edit)
        key = self._group_key(op)
        ops = [o for o in self.finance.between(op.date, op.date, op.type) if self._group_key(o) == key]
        ops.sort(key=lambda o: (o.created_at, o.id))
        back = "fin:exp" if op.type == "expense" else "fin:inc"
        if not ops:
            return self.ops_list_view(op.type, edit=edit)
        sign = "−" if op.type == "expense" else "+"
        lines = []
        for o in ops:
            orig = f" ({F.money(o.original_amount, o.original_currency)})" if o.original_currency else ""
            desc = (o.description or "").strip()
            note = f" {desc[:1].upper() + desc[1:]}" if desc and desc.lower() != (key[2] or "").lower() else ""
            lines.append(f"🕐 {self._op_time(o)} — {sign}{F.money(o.amount)}{orig}{note}")
        icon = F.emoji(op.category) if op.type == "expense" else "💵"
        day = f"{op.date.day} {F.MONTHS_GEN[op.date.month - 1]}"
        head = f"🎩 {icon} {key[2]}, {day} — {F.money(sum(o.amount for o in ops))}, Сэр!"
        def label(o: Operation) -> str:
            desc = (o.description or "").strip()
            extra = f" {desc[:1].upper() + desc[1:]}" if desc and desc.lower() != (key[2] or "").lower() else ""
            return f"❌ {self._op_time(o)} — {F.money(o.amount)}{extra}"[:60]
        rows = [[(label(o), f"fin:grpdel:{o.id}")] for o in ops[:10]]
        rows.append([("↩️ Назад", back)])
        return Reply(head + "\n\n" + "\n".join(lines), buttons=rows, edit=edit)

    def edit_list_view(self, edit: bool = True) -> Reply:
        ops = self.finance.latest(None, 8)
        debts = self.debts.active()
        if not ops and not debts:
            return Reply("🎩 Сэр, записей пока нет!", buttons=FIN_BUTTONS, edit=edit)
        rows = [[("❌ " + F.op_line(o), f"fin:del:{o.id}")] for o in ops]
        rows += [[("❌ " + self.debt_line(p, d), f"fin:deldebt:{d.id}")] for p, d in debts[:8]]
        rows.append([("↩️ Назад", "fin:stats")])
        return Reply("🎩 Какую запись удалить, Сэр?\n\nЧтобы изменить — просто напишите, например: "
                     "«Не 700, а 800» или «Сергей должен не 5000, а 6000».", buttons=rows, edit=edit)

    # ------------------------------------------------------------ долги
    def _resolve_person(self, r: BrainResult, action: str, create: bool) -> Person | Reply:
        name = r.person
        if not name:
            return self._ask(r, "person", "🎩 Разумеется, Сэр! Как зовут человека?")
        people = self.debts.find_people(name)
        if not people:
            if create:
                return self.debts.create_person(name)
            return Reply(f"🎩 Сэр, у меня нет записей о человеке по имени {name}!")
        if len(people) == 1:
            return people[0]
        self._set_pending(action, None, {"result": self._dump(r), "question": "выбор из списка", "choose": "person"})
        rows = [[(p.full_name, f"pick:{p.id}")] for p in people[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply(f"🎩 Сэр, уточните, пожалуйста, какой {name}?", buttons=rows)

    def _create_debt(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        direction = r.direction or direction_from_text(self._message)
        if r.direction and r.direction not in ("owes_me", "i_owe"):
            direction = direction_from_text(r.direction)
        money = self._money_in(r.amount_text, r.currency)
        if isinstance(money, Reply):
            return money
        if money is None:
            return self._ask(r, "amount_text", "🎩 Разумеется, Сэр! Какая сумма?")
        person = chosen or self._resolve_person(r, r.intent, create=True)
        if isinstance(person, Reply):
            return person
        if not direction:
            return self._ask(r, "direction", f"🎩 Сэр, кто кому должен: {person.full_name} вам или вы?")
        total = self.debts.add(person, direction, money[0])
        debt = self.debts.get(person, direction)
        self._set_last("debt", debt.id if debt else None)
        if direction == "owes_me":
            return Reply(f"🎩 Записал, Сэр!\n\n🤝 Вам должен: {person.full_name} — {F.money(total)}")
        return Reply(f"🎩 Записал, Сэр!\n\n💸 Ваш долг: {person.full_name} — {F.money(total)}")

    @staticmethod
    def debt_line(person: Person, debt: Debt) -> str:
        if debt.direction == "owes_me":
            return f"🤝 Вам должен: {person.full_name} — {F.money(debt.amount)}"
        return f"💸 Ваш долг: {person.full_name} — {F.money(debt.amount)}"

    def _last_debt(self) -> Optional[Debt]:
        ctx = self._ctx()
        if ctx.entity_type == "debt" and ctx.entity_id:
            return self.debts.by_id(ctx.entity_id)
        return None

    def _ambiguous_debts(self, r: BrainResult, items: list[tuple[Person, Debt]]) -> Reply:
        self._set_pending(r.intent, None, {"result": self._dump(r), "question": "выбор из списка", "choose": "debt"})
        rows = [[(self.debt_line(p, d), f"pick:{d.id}")] for p, d in items[:8]]
        rows.append([("↩️ Отмена", "pick:cancel")])
        return Reply("🎩 Сэр, уточните, пожалуйста, какой именно долг?", buttons=rows)

    def _find_debt(self, r: BrainResult) -> Debt | Reply | None:
        """Какой долг имеет в виду пользователь: по имени, по последнему разговору или единственный."""
        direction = r.direction or direction_from_text(self._message)
        if r.person:
            person = self._resolve_person(r, r.intent, create=False)
            if isinstance(person, Reply):
                return person
            items = [(person, d) for d in (self.debts.get(person, x) for x in ("owes_me", "i_owe")) if d]
            if not items:
                return Reply(f"🎩 Сэр, долгов с человеком по имени {person.full_name} нет!")
            if len(items) > 1 and direction:
                items = [(p, d) for p, d in items if d.direction == direction] or items
            return items[0][1] if len(items) == 1 else self._ambiguous_debts(r, items)
        last = self._last_debt()
        if last:
            return last
        items = self.debts.active(direction)
        if not items:
            return None
        return items[0][1] if len(items) == 1 else self._ambiguous_debts(r, items)

    def _update_debt(self, r: BrainResult, chosen: Optional[Debt] = None) -> Reply:
        debt = chosen or self._find_debt(r)
        if isinstance(debt, Reply):
            return debt
        if not debt:
            return Reply("🎩 Сэр, я не нашёл такой долг! Уточните, пожалуйста, чей именно?")
        person = self.debts.person(debt.person_id)
        money = self._money_in(r.new_amount_text or r.amount_text, r.currency)
        if isinstance(money, Reply):
            return money
        if money is None:
            self._set_last("debt", debt.id)
            return self._ask(r, "new_amount_text", "🎩 Сэр, какая теперь сумма?\n\n" + self.debt_line(person, debt))
        updated = self.debts.set_total(person, debt.direction, money[0])
        self._set_last("debt", updated.id)
        return Reply("🎩 Готово, Сэр! Исправил долг!\n\n" + self.debt_line(person, updated))

    def _repay_debt(self, r: BrainResult, chosen: Optional[Person] = None) -> Reply:
        person = chosen or self._resolve_person(r, r.intent, create=False)
        if isinstance(person, Reply):
            return person
        direction = r.direction or direction_from_text(self._message)
        existing = [d for d in ("owes_me", "i_owe") if self.debts.get(person, d)]
        if not existing:
            return Reply(f"🎩 Сэр, долгов с человеком по имени {person.full_name} нет!")
        if direction not in existing:
            direction = existing[0] if len(existing) == 1 else direction
        if not direction:
            return self._ask(r, "direction", f"🎩 Сэр, кто вернул долг: {person.full_name} вам или вы?")
        money = self._money_in(r.amount_text, r.currency)
        if isinstance(money, Reply):
            return money
        paid, left = self.debts.repay(person, direction, money[0] if money else None)
        debt = self.debts.get(person, direction)
        self._set_last("debt", debt.id if debt else None)
        if left > 0:
            return Reply(f"🎩 Отлично, Сэр!\n\n{person.full_name}: погашено {F.money(paid)}, осталось {F.money(left)}")
        return Reply(f"🎩 Великолепно, Сэр! {person.full_name} — долг полностью погашен!")

    def _delete_debt(self, r: BrainResult, chosen: Optional[Person | Debt] = None) -> Reply:
        if isinstance(chosen, Debt) or (not chosen and not r.person):
            debt = chosen if isinstance(chosen, Debt) else self._find_debt(r)
            if isinstance(debt, Reply):
                return debt
            if not debt:
                return Reply("🎩 Сэр, я не нашёл такой долг! Уточните, пожалуйста, чей именно?")
            person = self.debts.person(debt.person_id)
            self.debts.remove(person, debt.direction)
            self._set_last("debt", None)
            return Reply(f"🎩 Удалил долг, Сэр!\n\n❌ {self.debt_line(person, debt)}")
        person = chosen or self._resolve_person(r, r.intent, create=False)
        if isinstance(person, Reply):
            return person
        direction = r.direction or direction_from_text(self._message)
        directions = [direction] if direction else ["owes_me", "i_owe"]
        removed = False
        for d in directions:
            if self.debts.get(person, d):
                self.debts.remove(person, d)
                removed = True
        if not removed:
            return Reply(f"🎩 Сэр, долгов с человеком по имени {person.full_name} нет!")
        return Reply(f"🎩 Удалил долг, Сэр! {person.full_name} больше не в списке долгов!")

    def debts_view(self, direction: Optional[str] = None, edit: bool = False) -> Reply:
        if direction == "owes_me":
            items = self.debts.active("owes_me")
            if not items:
                return Reply("🎩 Сэр, вам никто не должен!", buttons=FIN_BUTTONS, edit=edit)
            lines = "\n".join(f"🤝 {p.full_name} — {F.money(d.amount)}" for p, d in items)
            return Reply(f"🎩 Вот кто вам должен, Сэр!\n\n{lines}", buttons=FIN_BUTTONS, edit=edit)
        if direction == "i_owe":
            items = self.debts.active("i_owe")
            if not items:
                return Reply("🎩 Сэр, вы никому не должны!", buttons=FIN_BUTTONS, edit=edit)
            lines = "\n".join(f"💸 {p.full_name} — {F.money(d.amount)}" for p, d in items)
            return Reply(f"🎩 Вот кому вы должны, Сэр!\n\n{lines}", buttons=FIN_BUTTONS, edit=edit)
        owes, mine = self.debts.active("owes_me"), self.debts.active("i_owe")
        if not owes and not mine:
            return Reply("🎩 Долгов нет, Сэр! Все в расчёте!", buttons=FIN_BUTTONS, edit=edit)
        parts = []
        if owes:
            parts.append("🤝 Вам должны:\n" + "\n".join(f"{p.full_name} — {F.money(d.amount)}" for p, d in owes))
        if mine:
            parts.append("💸 Вы должны:\n" + "\n".join(f"{p.full_name} — {F.money(d.amount)}" for p, d in mine))
        return Reply("🎩 Разумеется, Сэр!\n\n" + "\n\n".join(parts), buttons=FIN_BUTTONS, edit=edit)

    def _show_debts(self, r: BrainResult) -> Reply:
        t = _norm(self._message or "")
        direction = r.direction
        if not direction:
            if re.search(r"мне\s+должн|должн\w*\s+мне|кто\s+(мне\s+)?должен", t):
                direction = "owes_me"
            elif re.search(r"я\s+должен|я\s+должна|кому\s+я", t):
                direction = "i_owe"
        return self.debts_view(direction)

    # ------------------------------------------------------------ валюты
    def rates_view(self, only: Optional[str] = None, edit: bool = False) -> Reply:
        rates = self._rates
        if not rates:
            return Reply(T.AI_UNAVAILABLE, buttons=FIN_BUTTONS, edit=edit)
        codes = ["USD", "EUR", "CNY"]
        if only and only not in codes and only in rates.rub_per_unit:
            codes.append(only)
        lines = [x for x in (self._rate_line(code, rates) for code in codes) if x]
        head = f"🎩 Курсы ЦБ на {_day_short(rates.day)}, Сэр!"
        return Reply(head + "\n\n" + "\n".join(lines), buttons=FIN_BUTTONS, edit=edit)

    @staticmethod
    def _rate_line(code: str, rates) -> Optional[str]:
        v, prev = rates.rub_per_unit.get(code), rates.prev_rub_per_unit.get(code)
        if v is None:
            return None
        per = 1
        while v * per < 1 and per < 10000:          # вона, сум, донг: считаем за 100 / 1000 / 10000
            per *= 10
        delta = round((v - prev) * per, 2) if prev else 0
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "="
        unit = f"за {per}: " if per > 1 else ""
        return f"{FLAGS.get(code, code)} — {unit}{v * per:.2f} ₽ ({arrow} {abs(delta):.2f})".replace(".", ",")

    async def try_quick_rate(self, text: str) -> Optional[Reply]:
        """«лира», «курс лиры», «сколько стоит тенге?» — сразу курс ЦБ, без ИИ."""
        t = _norm(text).strip(" ?!.")
        if re.search(r"\d", t):
            return None
        rest = RATE_WORDS.sub(" ", t).split()
        code = parse_currency(" ".join(rest)) if rest else None
        if not code or code == "RUB" or len(rest) > 3:
            return None
        self._rates = await self.currency.get()
        if not self._rates:
            return Reply(T.AI_UNAVAILABLE, buttons=FIN_BUTTONS)
        line = self._rate_line(code, self._rates)
        if not line:
            return Reply("🎩 Сэр, курса этой валюты у ЦБ нет!", buttons=FIN_BUTTONS)
        return Reply(f"🎩 Курс ЦБ на {_day_short(self._rates.day)}, Сэр!\n\n{line}\n\n"
                     f"💱 Пересчитать можно так: «100 {code} в рублях».", buttons=FIN_BUTTONS)

    def _show_rates(self, r: BrainResult) -> Reply:
        return self.rates_view(parse_currency(r.currency) or parse_currency(self._message))

    def _convert(self, r: BrainResult) -> Reply:
        if not self._rates:
            return Reply(T.AI_UNAVAILABLE)
        amount, src, dst = parse_conversion(self._message or "")
        amount = parse_amount(r.amount_text) or amount or 1
        src = parse_currency(r.currency) or parse_currency(r.amount_text) or src
        dst = parse_currency(r.convert_to) or dst
        if not src and dst:
            src = "RUB"
        if src and not dst:
            dst = "RUB" if src != "RUB" else None
        if not src or not dst:
            return Reply("🎩 Сэр, уточните, пожалуйста, какую валюту пересчитать?")
        result = self._rates.convert(amount, src, dst)
        if result is None:
            return Reply("🎩 Сэр, ЦБ не публикует курс этой валюты!")
        return Reply(f"🎩 Разумеется, Сэр!\n\n{F.money(amount, src)} = {F.money(result, dst)} по курсу ЦБ!")

    # ------------------------------------------------------------ кнопки раздела
    def finance_callback(self, parts: list[str]) -> Reply:
        action = parts[1]
        if action == "stats":
            return self.statistics_view(edit=True)
        if action == "exp":
            return self.ops_list_view("expense", edit=True)
        if action == "inc":
            return self.ops_list_view("income", edit=True)
        if action == "debts":
            return self.debts_view(edit=True)
        if action == "rates":
            return self.rates_view(edit=True)
        if action == "search":
            return Reply("🎩 Что найти, Сэр?\n\nНапример: «Сколько я потратил на продукты за месяц?»",
                         buttons=FIN_BUTTONS, edit=True)
        if action == "edit":
            return self.edit_list_view()
        if action == "grp" and len(parts) == 3 and parts[2].isdigit():
            return self.group_view(int(parts[2]))
        if action == "grpdel" and len(parts) == 3 and parts[2].isdigit():
            op = self.finance.get(int(parts[2]))
            if not op:
                return self.ops_list_view("expense", edit=True)
            key = self._group_key(op)
            rest = [o for o in self.finance.between(op.date, op.date, op.type)
                    if self._group_key(o) == key and o.id != op.id]
            self.finance.delete([op])
            return self.group_view(rest[0].id) if rest else self.ops_list_view(op.type, edit=True)
        if action == "deldebt" and len(parts) == 3 and parts[2].isdigit():
            debt = self.debts.by_id(int(parts[2]))
            person = self.debts.person(debt.person_id) if debt else None
            if person:
                self.debts.remove(person, debt.direction)
            return self.edit_list_view()
        if action == "del" and len(parts) == 3 and parts[2].isdigit():
            op = self.finance.get(int(parts[2]))
            if op:
                self.finance.delete([op])
            return self.edit_list_view()
        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)

    def confirm_delete_ops(self, ids: str) -> Reply:
        ops = [o for o in (self.finance.get(int(i)) for i in ids.split(",") if i.isdigit()) if o]
        self.finance.delete(ops)
        self._set_last("finance", None)
        return Reply(f"🎩 Готово, Сэр! Удалил записи: {len(ops)}!", edit=True)
