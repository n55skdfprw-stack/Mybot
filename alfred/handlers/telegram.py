"""Связь Telegram ↔ ядро Альфреда. Здесь нет бизнес-логики — только доступ и отправка сообщений."""

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, User

from ..core.access import Access, Visitor
from ..core.admin import Admin
from ..core.reply import Reply
from ..core.security import MAX_TEXT, RateLimiter
from ..services import search
from ..ui import texts as T
from ..ui.address import MAM, SIR, asked_address, personalize
from ..ui.keyboards import inline, main_menu

log = logging.getLogger(__name__)


async def send_reply(bot: Bot, chat_id: int, reply: Reply, owner: bool = False,
                     address: str | None = None) -> None:
    reply = personalize(reply, address)
    markup = inline(reply.buttons) or main_menu(owner)
    await bot.send_message(chat_id, reply.text, reply_markup=markup)
    for extra in reply.extra:
        await bot.send_message(chat_id, extra.text, reply_markup=inline(extra.buttons) or main_menu(owner))


def build_router(access: Access, admin: Admin) -> Router:
    router = Router()
    limiter = RateLimiter()
    # Только личные сообщения: в группах Альфред молчит.
    router.message.filter(F.chat.type == "private")

    def is_word(text: str | None, words: tuple) -> bool:
        return search.normalize(text or "").strip(" !.") in words

    def visitor(user: User) -> Visitor:
        return access.who(user.id, user.username, user.full_name)

    async def ask_address(message: Message) -> None:
        await message.answer(T.ASK_ADDRESS, reply_markup=inline([[("🎩 Сэр", "addr:sir"), ("👒 Мэм", "addr:mam")]]))

    async def gate(message: Message, bot: Bot, starting: bool = False) -> Visitor | None:
        """Пускаем владельца и приглашённых. Остальным — вежливый отказ.
        При первом знакомстве сначала спрашиваем, как обращаться: «Сэр» или «Мэм»."""
        if message.from_user is None or message.from_user.is_bot:
            return None
        v = visitor(message.from_user)
        if v.kind in ("stranger", "blocked"):
            if limiter.stranger_may_get_reply(message.from_user.id):
                await message.answer(T.INVITE_ONLY if v.kind == "stranger" else T.BLOCKED,
                                     reply_markup=ReplyKeyboardRemove())
            return None
        if v.kind != "owner":
            ok, warn = limiter.allow(message.from_user.id)
            if not ok:
                if warn:
                    await message.answer(personalize(Reply(T.TOO_FAST), v.account.address).text)
                return None
        skip, tell = limiter.too_long(message.from_user.id, len(message.text or ""), MAX_TEXT)
        if skip:
            if tell:
                await message.answer(personalize(Reply(T.TOO_LONG.format(n=MAX_TEXT)), v.account.address).text)
            return None
        if v.kind != "owner" and access.stopped_for_all:
            return None                              # ⏹ остановлен для всех — гостям молчим
        if v.account and v.account.paused:
            if is_word(message.text, T.RESUME_WORDS):
                access.users.set_paused(v.account.id, False)
                await send_reply(bot, message.chat.id, Reply(T.RESUMED), owner=v.kind == "owner",
                                 address=v.account.address)
            return None                              # ⏸ на паузе — молчим
        if v.kind == "new":
            owner = access.owner
            note = personalize(Reply(f"🎩 Сэр, {v.account.label} принял приглашение и теперь пользуется Альфредом!"),
                               owner.address)
            try:
                await bot.send_message(access.owner_tg, note.text)
            except Exception:
                log.exception("Не удалось уведомить владельца")
            v.kind = "user"
        if v.account.address is None and (starting or v.kind == "user"):
            await ask_address(message)
            return None
        return v

    async def greet(message: Message, bot: Bot):
        v = await gate(message, bot, starting=True)
        if not v:
            return
        reply = personalize(access.alfred_for(v.account).start(), v.account.address)
        await message.answer(reply.text, reply_markup=main_menu(v.kind == "owner"))

    @router.message(CommandStart())
    async def on_start(message: Message, bot: Bot):
        await greet(message, bot)

    @router.message(F.text.func(lambda t: search.normalize(t).strip(" !.") in T.START_WORDS))
    async def on_hello(message: Message, bot: Bot):
        await greet(message, bot)

    @router.message(Command("pause", "resume"))
    async def on_pause_command(message: Message, bot: Bot):
        await pause_or_resume(message, bot, message.text.startswith("/pause"))

    @router.message(F.text.func(lambda t: search.normalize(t or "").strip(" !.") in T.PAUSE_WORDS))
    async def on_pause_words(message: Message, bot: Bot):
        await pause_or_resume(message, bot, True)

    async def pause_or_resume(message: Message, bot: Bot, pause: bool):
        if not pause:
            v = await gate(message, bot)             # на паузе gate сам снимет её по «/resume»
            if v:
                await send_reply(bot, message.chat.id, Reply("🎩 Я и так на связи, Сэр!"),
                                 owner=v.kind == "owner", address=v.account.address)
            return
        v = await gate(message, bot)
        if not v:
            return
        access.users.set_paused(v.account.id, True)
        await send_reply(bot, message.chat.id, Reply(T.PAUSED, buttons=[[("▶️ Продолжить", "pause:off")]]),
                         owner=v.kind == "owner", address=v.account.address)

    @router.message(Command("reset", "clean"))
    async def on_reset(message: Message, bot: Bot):
        v = await gate(message, bot)
        if not v:
            return
        reply = access.alfred_for(v.account).reset_request()
        await message.answer(reply.text, reply_markup=inline(reply.buttons))

    @router.message(F.text)
    async def on_text(message: Message, bot: Bot):
        v = await gate(message, bot)
        if not v:
            return
        owner = v.kind == "owner"
        addr = asked_address(message.text)
        if addr:
            access.users.set_address(v.account.id, addr)
            await send_reply(bot, message.chat.id, Reply(f"🎩 Как скажете, {addr}!"), owner=owner, address=addr)
            return
        if owner:
            if message.text == T.MENU_SYSTEM:
                await bot.send_chat_action(message.chat.id, "typing")
                await send_reply(bot, message.chat.id, await admin.system_view(), owner=True,
                                 address=v.account.address)
                return
            reply = admin.command(message.text)
            if reply:
                await send_reply(bot, message.chat.id, reply, owner=True, address=v.account.address)
                return
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await access.alfred_for(v.account).handle_text(message.text)
        await send_reply(bot, message.chat.id, reply, owner=owner, address=v.account.address)

    @router.message()
    async def on_other(message: Message, bot: Bot):
        v = await gate(message, bot)
        if v:
            await send_reply(bot, message.chat.id, Reply("🎩 Сэр, пока я понимаю только текст!"),
                             owner=v.kind == "owner", address=v.account.address)

    @router.callback_query()
    async def on_callback(callback: CallbackQuery, bot: Bot):
        v = visitor(callback.from_user)
        if v.kind not in ("owner", "user", "new"):
            await callback.answer("Доступ закрыт")
            return
        if v.kind != "owner" and not limiter.allow(callback.from_user.id)[0]:
            await callback.answer("Не так быстро 🙂")
            return
        owner = v.kind == "owner"
        data = callback.data or ""
        if not owner and access.stopped_for_all:
            await callback.answer()
            return
        if v.account.paused:
            await callback.answer()
            if data == "pause:off":
                access.users.set_paused(v.account.id, False)
                if callback.message:
                    try:
                        await callback.message.edit_text(personalize(Reply(T.RESUMED), v.account.address).text)
                    except TelegramBadRequest:
                        pass
            return
        if data == "pause:off":
            await callback.answer("Альфред и так работает")
            return
        if data in ("addr:sir", "addr:mam"):
            addr = SIR if data == "addr:sir" else MAM
            access.users.set_address(v.account.id, addr)
            await callback.answer()
            if callback.message:
                try:
                    await callback.message.edit_text(f"🎩 Обращение: {addr} ✅")
                except TelegramBadRequest:
                    pass
            intro = T.INTRO.format(addr=addr) + ("" if owner else T.INTRO_LIMIT.format(limit=access.limit_of(v.account)))
            await bot.send_message(callback.from_user.id, intro, reply_markup=main_menu(owner))
            return
        if data.startswith("adm:"):
            if not owner:
                await callback.answer("Доступ закрыт")
                return
            was_stopped = access.stopped_for_all
            reply = await admin.callback(data)
            if data in ("adm:stopall", "adm:startall") and access.stopped_for_all != was_stopped \
                    and access.notify_maintenance:
                note = T.MAINT_ON if access.stopped_for_all else T.MAINT_OFF
                for guest in access.guests_to_notify():
                    try:
                        await send_reply(bot, guest.telegram_id, Reply(note), address=guest.address)
                    except Exception:
                        log.exception("Не удалось предупредить %s о техработах", guest.label)
        else:
            reply = await access.alfred_for(v.account).handle_callback_async(data)
        reply = personalize(reply, v.account.address)
        await callback.answer(reply.toast or None)
        msg = callback.message
        try:
            if reply.edit and msg:
                await msg.edit_text(reply.text, reply_markup=inline(reply.buttons))
                return
            if reply.clear_source_buttons and msg:
                await msg.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest as exc:
            # Например, «сообщение не изменилось» — это не ошибка для пользователя.
            log.info("Edit skipped: %s", exc)
            if reply.edit:
                return
        await send_reply(bot, callback.from_user.id, reply, owner=owner, address=v.account.address)

    return router


def build_dispatcher(access: Access, admin: Admin) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router(access, admin))
    return dp
