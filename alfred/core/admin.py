"""Панель владельца: 👥 пользователи и ⚙️ система. Чужие данные здесь не показываются — только статистика."""

import os
import re
from datetime import datetime, timezone
from typing import Optional

from .. import VERSION
from ..database.repositories import Account, clean_username
from ..services import search
from ..services.weather import SPB, WeatherUnavailable
from ..ui import texts as T
from .access import Access
from .reply import Reply

NAME = r"@([a-z0-9_]{4,32})"
INVITE_RE = re.compile(rf"(?:добав\w*|пригласи\w*|дай доступ\w*)\s+(?:пользовател\w*\s+)?{NAME}")
BLOCK_RE = re.compile(rf"(?:заблокир\w*|закрой доступ\w*|отключи\w*)\s+(?:для\s+)?{NAME}")
UNBLOCK_RE = re.compile(rf"(?:разблокир\w*|открой доступ\w*|верни доступ\w*)\s+(?:для\s+)?{NAME}")
DELETE_RE = re.compile(rf"(?:удали\w*|убери\w*)\s+(?:пользовател\w*\s+)?{NAME}")
UNLIMITED_RE = re.compile(rf"(?:безлимит\w*|без\s+лимита)\s+(?:для\s+)?{NAME}|{NAME}\s+(?:безлимит\w*|без\s+лимита)")
LIMIT_RE = re.compile(rf"(?:лимит\w*\s+(?:для\s+)?{NAME}\D+(\d{{1,4}})|{NAME}\s+лимит\w*\D+(\d{{1,4}}))")


class Admin:
    def __init__(self, access: Access, llm=None):
        self.access = access
        self.users = access.users
        self.llm = llm

    # ------------------------------------------------------------ помощники
    def _now(self) -> datetime:
        return self.access.clock() if self.access.clock else datetime.now(self.access.tz)

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _seen(self, iso: Optional[str]) -> str:
        if not iso:
            return "ещё не заходил"
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(self.access.tz)
        days = (self._now().date() - dt.date()).days
        day = "сегодня" if days == 0 else "вчера" if days == 1 else f"{dt.day} {T.MONTHS_GEN[dt.month - 1][:3]}"
        return f"{day} в {dt:%H:%M}"

    def _find(self, username: str) -> Optional[Account]:
        acc = self.users.by_username(username)
        return acc if acc and acc.role != "owner" else None

    # ------------------------------------------------------------ команды словами
    def command(self, text: str) -> Optional[Reply]:
        """Команды владельца обычной речью. None — это не команда, пусть разбирается ИИ."""
        t = search.normalize(text).strip()
        if t in (search.normalize(T.MENU_USERS), "пользователи", "кто пользуется альфредом"):
            return self.users_view()
        m = INVITE_RE.search(t)
        if m:
            return self.invite(m.group(1))
        m = UNLIMITED_RE.search(t)
        if m:
            acc = self._find(m.group(1) or m.group(2))
            if not acc:
                return Reply(f"🎩 Сэр, пользователя @{m.group(1) or m.group(2)} у меня нет!")
            self.users.set_limit(acc.id, 0)
            return Reply(f"🎩 Готово, Сэр!\n\n♾ {acc.label}: безлимит на сообщения ИИ")
        m = LIMIT_RE.search(t)
        if m:
            name, value = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            return self.set_limit(name, int(value))
        m = UNBLOCK_RE.search(t)
        if m:
            return self.set_access(m.group(1), open_=True)
        m = BLOCK_RE.search(t)
        if m:
            return self.set_access(m.group(1), open_=False)
        m = DELETE_RE.search(t)
        if m:
            acc = self._find(m.group(1))
            if acc:
                return self._confirm_delete(acc, edit=False)
            if clean_username(m.group(1)) in self.users.invites():
                return self.cancel_invite(m.group(1), edit=False)
            return Reply(f"🎩 Сэр, пользователя @{m.group(1)} у меня нет!")
        return None

    def invite(self, username: str) -> Reply:
        name = clean_username(username)
        acc = self.users.by_username(name)
        if acc and acc.role == "owner":
            return Reply("🎩 Сэр, это же вы!")
        if acc:
            if acc.status == "blocked":
                return self.set_access(name, open_=True)
            return Reply(f"🎩 Сэр, @{name} уже пользуется Альфредом!")
        self.users.add_invite(name)
        return Reply(f"🎩 Приглашение создано, Сэр!\n\n⏳ @{name}\n\nПусть @{name} найдёт меня в Telegram и нажмёт "
                     f"«Start» — я узнаю его по имени и открою доступ.\nЛимит: {self.access.default_limit} сообщений "
                     "в день (кнопки меню — без ограничений).",
                     buttons=[[(f"❌ Отменить приглашение @{name}"[:60], f"adm:uninv:{name}")],
                              [("👥 Пользователи", "adm:users")]])

    def cancel_invite(self, username: str, edit: bool = True) -> Reply:
        name = clean_username(username)
        self.users.take_invite(name)
        if edit:
            return self.users_view(edit=True)
        return Reply(f"🎩 Приглашение отменено, Сэр!\n\n❌ @{name}")

    def set_limit(self, username: str, value: int) -> Reply:
        acc = self._find(username)
        if not acc:
            return Reply(f"🎩 Сэр, пользователя @{clean_username(username)} у меня нет!")
        self.users.set_limit(acc.id, max(1, value))
        return Reply(f"🎩 Готово, Сэр!\n\n🤖 Лимит для {acc.label}: {max(1, value)} сообщений в день")

    def set_access(self, username: str, open_: bool) -> Reply:
        acc = self._find(username)
        if not acc:
            return Reply(f"🎩 Сэр, пользователя @{clean_username(username)} у меня нет!")
        self.users.set_status(acc.id, "active" if open_ else "blocked")
        if open_:
            return Reply(f"🎩 Готово, Сэр! Доступ открыт!\n\n✅ {acc.label}")
        return Reply(f"🎩 Готово, Сэр! Доступ закрыт, данные сохранены.\n\n⛔ {acc.label}")

    # ------------------------------------------------------------ экраны
    def _line(self, acc: Account) -> str:
        if acc.role == "owner":
            return f"👑 {acc.label} — вы"
        if acc.status == "blocked":
            return f"⛔ {acc.label} · доступ закрыт"
        used = self.users.usage(acc.id, self._today())
        limit = "∞" if acc.unlimited else self.access.limit_of(acc)
        return f"👤 {acc.label} · сегодня {used}/{limit} · был {self._seen(acc.last_seen)}"

    def users_view(self, edit: bool = False) -> Reply:
        accounts = self.users.accounts()
        invites = self.users.invites()
        lines = [self._line(a) for a in accounts] + [f"⏳ @{n} · приглашён, ещё не заходил" for n in invites]
        rows = [[(self._line(a).split(" · ")[0][:40], f"adm:u:{a.id}")] for a in accounts if a.role != "owner"]
        rows += [[(f"❌ Отменить приглашение @{n}"[:60], f"adm:uninv:{n}")] for n in invites]
        text = "🎩 Пользователи, Сэр!\n\n" + "\n".join(lines) + "\n\nДобавить: «Добавь @имя»"
        return Reply(text, buttons=rows or None, edit=edit)

    def user_card(self, user_id: int, edit: bool = True) -> Reply:
        acc = self.users.by_id(user_id)
        if not acc or acc.role == "owner":
            return self.users_view(edit=edit)
        limit = "∞ (безлимит)" if acc.unlimited else self.access.limit_of(acc)
        state = "✅ доступ открыт" if acc.status == "active" else "⛔ доступ закрыт (данные сохранены)"
        if acc.paused:
            state += " · ⏸ сам поставил паузу"
        joined = datetime.fromisoformat(acc.created_at)
        text = (f"🎩 {acc.label}, Сэр!\n\n👤 Пользователь · {state}\n"
                f"📅 С нами с {joined.day} {T.MONTHS_GEN[joined.month - 1]}\n"
                f"👀 Последний раз: {self._seen(acc.last_seen)}\n"
                f"🤖 Сообщений ИИ: сегодня {self.users.usage(acc.id, self._today())} из {limit} · "
                f"всего {self.users.usage_total(acc.id)}")
        if acc.unlimited:
            rows = [[(f"🔢 Вернуть лимит {self.access.default_limit}", f"adm:unl:{acc.id}:0")]]
        else:
            rows = [[("➖ Лимит −10", f"adm:lim:{acc.id}:-10"), ("➕ Лимит +10", f"adm:lim:{acc.id}:10")],
                    [("♾ Безлимит", f"adm:unl:{acc.id}:1")]]
        if acc.status == "active":
            rows.append([("⛔ Закрыть доступ (данные останутся)", f"adm:block:{acc.id}")])
        else:
            rows.append([("✅ Открыть доступ", f"adm:open:{acc.id}")])
        rows.append([("🗑 Удалить вместе с данными", f"adm:del:{acc.id}")])
        rows.append([("↩️ Назад", "adm:users")])
        return Reply(text, buttons=rows, edit=edit)

    def _confirm_delete(self, acc: Account, edit: bool = True) -> Reply:
        rows = [[("🗑 Да, удалить всё", f"adm:delyes:{acc.id}")], [("↩️ Нет", f"adm:u:{acc.id}")]]
        return Reply(f"🎩 Сэр, удалить {acc.label} и ВСЕ его данные?\n\nДела, заметки, финансы, медкарта — "
                     "всё будет стёрто, вернуть будет невозможно!\nЕсли нужно только закрыть доступ — "
                     "выберите «⛔ Закрыть доступ».", buttons=rows, edit=edit)

    async def system_view(self, edit: bool = False) -> Reply:
        ok = lambda flag: "✅" if flag else "❌"     # noqa: E731
        ai = await self.llm.check() if self.llm else False
        rates = await self.access.currency.get()
        try:
            await self.access.weather.forecast(SPB)
            wx = True
        except WeatherUnavailable:
            wx = False
        up = self._now() - self.access.started
        hours, minutes = int(up.total_seconds() // 3600), int(up.total_seconds() % 3600 // 60)
        try:
            size = os.path.getsize(self.access.db.path) / 1024 / 1024
            db = f"{size:.1f} МБ"
        except (OSError, AttributeError):
            db = "—"
        accounts = self.users.accounts()
        today = self._today()
        owner_used = sum(self.users.usage(a.id, today) for a in accounts if a.role == "owner")
        guests_used = sum(self.users.usage(a.id, today) for a in accounts if a.role != "owner")
        guests = [a for a in accounts if a.role != "owner"]
        text = (f"🎩 Система, Сэр!\n\n🎩 Альфред — версия {VERSION}\n⏱ Работает без перерыва: {hours} ч {minutes} мин\n"
                f"🤖 ИИ (GigaChat): {ok(ai)}\n💱 Курсы ЦБ: {ok(rates)}\n🌤 Погода: {ok(wx)}\n💾 База: {db}\n\n"
                f"👥 Пользователей: {len(guests)} · приглашений: {len(self.users.invites())}\n"
                f"📊 Сообщений ИИ сегодня: вы — {owner_used}, гости — {guests_used}\n"
                f"🔢 Лимит для гостей по умолчанию: {self.access.default_limit} в день")
        if self.access.stopped_for_all:
            text += ("\n\n⏹ Альфред остановлен для всех: никому ничего не пишет, гостям не отвечает. "
                     "Вам — отвечает, чтобы можно было запустить обратно.")
            stop = [("▶️ Запустить для всех", "adm:startall")]
        else:
            stop = [("⏹ Остановить для всех", "adm:stopall")]
        return Reply(text, buttons=[[("🔄 Обновить", "adm:sys"), ("👥 Пользователи", "adm:users")], stop], edit=edit)

    # ------------------------------------------------------------ кнопки
    async def callback(self, data: str) -> Reply:
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        num = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        if action == "users":
            return self.users_view(edit=True)
        if action == "sys":
            return await self.system_view(edit=True)
        if action == "uninv" and len(parts) > 2:
            return self.cancel_invite(parts[2])
        if action == "u" and num:
            return self.user_card(num)
        if action == "lim" and num and len(parts) > 3:
            acc = self.users.by_id(num)
            if acc and not acc.unlimited:
                self.users.set_limit(acc.id, max(10, self.access.limit_of(acc) + int(parts[3])))
            return self.user_card(num)
        if action == "unl" and num and len(parts) > 3:
            self.users.set_limit(num, 0 if parts[3] == "1" else None)   # None — лимит по умолчанию
            return self.user_card(num)
        if action in ("stopall", "startall"):
            self.access.stop_for_all(action == "stopall")
            return await self.system_view(edit=True)
        if action in ("block", "open") and num:
            self.users.set_status(num, "blocked" if action == "block" else "active")
            return self.user_card(num)
        if action == "del" and num:
            acc = self.users.by_id(num)
            return self._confirm_delete(acc) if acc and acc.role != "owner" else self.users_view(edit=True)
        if action == "delyes" and num:
            acc = self.users.by_id(num)
            if acc and acc.role != "owner":
                self.access.db.delete_user(acc.id)
                self.access.forget(acc.id)
                return Reply(f"🎩 Готово, Сэр! {acc.label} удалён вместе с данными.", edit=True)
            return self.users_view(edit=True)
        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)
