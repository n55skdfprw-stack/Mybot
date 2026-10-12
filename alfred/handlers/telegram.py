"""Связь Telegram ↔ ядро Альфреда. Здесь нет бизнес-логики — только доступ и отправка сообщений."""

import asyncio
import logging
import re
from dataclasses import replace

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter,
                                TelegramServerError)
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ErrorEvent, Message, ReplyKeyboardRemove, User

from ..core.access import Access, Visitor
from ..core.admin import Admin
from ..core.reply import Reply
from ..core.security import MAX_TEXT, RateLimiter, SeenOnce
from ..core.selftest import run_selftest
from ..core.university import sync_and_report
from ..services import search
from ..ui import texts as T
from ..ui.address import MAM, SIR, asked_address, personalize
from ..ui.keyboards import inline, main_menu

log = logging.getLogger(__name__)

UNI_RE = re.compile(r"\s*(?:альфред,?\s*)?(?:(?:обнови|синхронизируй|загрузи|подтяни|проверь)\s+)?"
                    r"(?:мо[её]\s+)?расписание\s+(?:вуза|университета|универа|пар)[\s.!?]*")


async def send_with_retry(bot: Bot, chat_id: int, text: str, **kw):
    """Отправка с повторами: если Telegram на секунду не ответил или просит подождать — пробуем ещё раз."""
    for attempt in range(3):
        try:
            return await bot.send_message(chat_id, text, **kw)
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(min(e.retry_after, 30))
        except (TelegramNetworkError, TelegramServerError):
            if attempt == 2:
                raise
            await asyncio.sleep(1 + attempt * 2)


TEXT_LIMIT = 4000          # Telegram не принимает сообщения длиннее 4096 символов


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Длинный ответ — несколькими сообщениями: режем по пустой строке, иначе по строке, в крайнем случае — по символам."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return [p for p in parts if p.strip()] or [text]


async def send_long(bot: Bot, chat_id: int, text: str, markup, **kw) -> None:
    """Отправить ответ любой длины; кнопки — под последним куском."""
    chunks = split_text(text)
    for i, chunk in enumerate(chunks):
        await send_with_retry(bot, chat_id, chunk, reply_markup=markup if i == len(chunks) - 1 else None, **kw)


async def send_reply(bot: Bot, chat_id: int, reply: Reply, owner: bool = False,
                     address: str | None = None) -> None:
    reply = personalize(reply, address)
    markup = inline(reply.buttons) or main_menu(owner)
    await send_long(bot, chat_id, reply.text, markup, protect_content=reply.protect)
    for extra in reply.extra:
        await send_long(bot, chat_id, extra.text, inline(extra.buttons) or main_menu(owner),
                        protect_content=extra.protect)


def build_router(access: Access, admin: Admin, backup=None) -> Router:
    router = Router()
    limiter = RateLimiter()
    seen = SeenOnce()
    forwards: dict[int, tuple[int, str | None]] = {}   # пересланный пост из канала ждёт ответа: (канал, текст)
    background: set = set()                             # фоновые задачи (самопроверка) — держим ссылки, пока идут
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
        if not seen.first_time(("m", message.chat.id, message.message_id)):
            return None                              # то же самое сообщение пришло второй раз — пропускаем
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

    @router.message(Command("privacy"))
    async def on_privacy(message: Message, bot: Bot):
        v = await gate(message, bot)
        if v:
            await send_reply(bot, message.chat.id, Reply(T.PRIVACY), owner=v.kind == "owner",
                             address=v.account.address)

    @router.message(Command("selftest"))
    async def on_selftest(message: Message, bot: Bot):
        v = await gate(message, bot)
        if not v:
            return
        if v.kind != "owner":
            await send_reply(bot, message.chat.id, Reply("🎩 Сэр, эта команда — только для хозяина Альфреда!"),
                             address=v.account.address)
            return
        start_selftest(bot, v)

    def start_selftest(bot: Bot, v: Visitor) -> None:
        """Самопроверка идёт 1–2 минуты в фоне: Альфред тем временем отвечает на сообщения как обычно."""
        async def go():
            try:
                await send_reply(bot, access.owner_tg, Reply("🎩 Запускаю самопроверку, Сэр! Это займёт 1–2 минуты."),
                                 owner=True, address=v.account.address)
                report = await run_selftest(access)
                await send_reply(bot, access.owner_tg, report, owner=True, address=v.account.address)
            except Exception:
                log.exception("Самопроверка по команде не удалась")
        task = asyncio.create_task(go())
        background.add(task)
        task.add_done_callback(background.discard)

    def restore_lessons(bot: Bot, v: Visitor) -> None:
        """После «Чистого листа» — сразу забрать пары с сайта вуза. Идёт в фоне, после ответа «Всё очищено»."""
        async def go():
            try:
                async with access.lock(v.account.id):
                    reply = await sync_and_report(access.alfred_for(v.account), access.university, access.users,
                                                  manual=True)
                if reply:
                    await send_reply(bot, access.owner_tg, reply, owner=True, address=v.account.address)
            except Exception:
                log.exception("Не удалось подгрузить пары после «Чистого листа»")
        task = asyncio.create_task(go())
        background.add(task)
        task.add_done_callback(background.discard)

    @router.message(Command("deleteme"))
    async def on_delete_me(message: Message, bot: Bot):
        v = await gate(message, bot)
        if v:
            await ask_delete_me(message.chat.id, bot, v)

    async def ask_delete_me(chat_id: int, bot: Bot, v: Visitor):
        if v.kind == "owner":
            await send_reply(bot, chat_id, Reply("🎩 Сэр, вы хозяин Альфреда — ваш аккаунт удалить нельзя. "
                                                         "Чтобы стереть записи, есть «🧹 Чистый лист»."),
                             owner=True, address=v.account.address)
            return
        await send_reply(bot, chat_id, Reply(T.DELETE_ME_ASK, buttons=[
            [("🗑 Да, удалить аккаунт", "me:delete")], [("↩️ Нет, оставить", "me:keep")]]),
                         address=v.account.address)

    @router.message(Command("reset", "clean"))
    async def on_reset(message: Message, bot: Bot):
        v = await gate(message, bot)
        if not v:
            return
        reply = access.alfred_for(v.account).reset_request()
        await message.answer(reply.text, reply_markup=inline(reply.buttons))

    @router.message(F.forward_origin)
    async def on_forward(message: Message, bot: Bot):
        """Пересланное сообщение. Если владелец переслал пост из канала — спрашиваем: подключить канал
        для резервных копий или разобрать текст как обычное сообщение (сами ничего не подключаем)."""
        v = await gate(message, bot)
        if not v:
            return
        origin = message.forward_origin
        text = message.text or message.caption
        if v.kind == "owner" and backup and getattr(origin, "type", "") == "channel" \
                and origin.chat.id != backup.channel:
            forwards[v.account.id] = (origin.chat.id, text)
            rows = [[("📁 Да, для резервных копий", "fwd:backup")]]
            if text:
                rows.append([("📝 Нет, это просто сообщение", "fwd:text")])
            rows.append([("↩️ Отмена", "fwd:no")])
            title = origin.chat.title or "без названия"
            await send_reply(bot, message.chat.id, Reply(
                f"🎩 Сэр, это сообщение из канала «{title}»!\n\nПодключить этот канал для резервных копий? "
                "Каждую ночь я буду класть туда копию базы. Прежние сообщения канала я не трогаю.", buttons=rows),
                owner=True, address=v.account.address)
            return
        if text:
            # сразу к разбору текста: повторный вход через on_text принял бы сообщение за дубль и промолчал
            async with access.lock(v.account.id):
                await handle_text(text, message.chat.id, bot, v)

    @router.message(F.document)
    async def on_document(message: Message, bot: Bot):
        """Владелец прислал файл копии — предлагаем восстановить базу из него."""
        v = await gate(message, bot)
        if not v:
            return
        name = message.document.file_name or "копия"
        if v.kind == "owner" and backup and name.endswith(".bin"):
            reply = await backup.ask_restore_upload(message.document.file_id, name)
            await send_reply(bot, message.chat.id, reply, owner=True, address=v.account.address)
            return
        await send_reply(bot, message.chat.id, Reply("🎩 Сэр, пока я понимаю только текст!"),
                         owner=v.kind == "owner", address=v.account.address)

    @router.message(F.text)
    async def on_text(message: Message, bot: Bot):
        v = await gate(message, bot)
        if not v:
            return
        async with access.lock(v.account.id):        # сообщения одного человека — строго по очереди
            await handle_text(message.text, message.chat.id, bot, v)

    async def handle_text(text: str, chat_id: int, bot: Bot, v: Visitor):
        owner = v.kind == "owner"
        if is_word(text, T.PRIVACY_WORDS):
            await send_reply(bot, chat_id, Reply(T.PRIVACY), owner=owner, address=v.account.address)
            return
        if is_word(text, T.DELETE_ME_WORDS):
            await ask_delete_me(chat_id, bot, v)
            return
        addr = asked_address(text)
        if addr:
            access.users.set_address(v.account.id, addr)
            await send_reply(bot, chat_id, Reply(f"🎩 Как скажете, {addr}!"), owner=owner, address=addr)
            return
        if owner and backup and re.fullmatch(
                r"\s*(?:альфред,?\s*)?(?:резерв|бэкап|backup|резервн\w*\s+копи\w*|проверка\s+резерва|"
                r"(?:сделай|создай|сними)\s+(?:резервную\s+)?копию)[\s.!?]*", text.lower()):
            # «Резерв», «Сделай копию», «Проверка резерва» — открываем 📁 Резерв, а не гадаем через ИИ
            wants_now = re.search(r"сделай|создай|сними", text.lower())
            reply = await backup.callback("bk:now" if wants_now else "bk:view", chat_id)
            reply = replace(reply, edit=False)
            await send_reply(bot, chat_id, reply, owner=True, address=v.account.address)
            return
        if owner and UNI_RE.fullmatch(text.lower()):
            # «Обнови расписание вуза» — сверяем с сайтом вуза сразу, а не ждём плановой проверки
            if access.university:
                await bot.send_chat_action(chat_id, "typing")
                reply = await sync_and_report(access.alfred_for(v.account), access.university, access.users,
                                              manual=True)
            else:
                reply = Reply("🎩 Сэр, расписание вуза ещё не подключено!\n\nДобавьте в Railway → Variables "
                              "UNI_LOGIN, UNI_PASSWORD и UNI_GROUP — и после перезапуска я подключусь сам.")
            await send_reply(bot, chat_id, reply, owner=True, address=v.account.address)
            return
        if owner:
            menu = T.menu_guess(text, owner=True)
            if text == T.MENU_SYSTEM or menu == T.MENU_SYSTEM:
                await bot.send_chat_action(chat_id, "typing")
                await send_reply(bot, chat_id, await admin.system_view(), owner=True, address=v.account.address)
                return
            reply = admin.command(menu if menu == T.MENU_USERS else text)
            if reply:
                await send_reply(bot, chat_id, reply, owner=True, address=v.account.address)
                return
        await bot.send_chat_action(chat_id, "typing")
        reply = await access.alfred_for(v.account).handle_text(text)
        await send_reply(bot, chat_id, reply, owner=owner, address=v.account.address)

    @router.message()
    async def on_other(message: Message, bot: Bot):
        v = await gate(message, bot)
        if v:
            await send_reply(bot, message.chat.id, Reply("🎩 Сэр, пока я понимаю только текст!"),
                             owner=v.kind == "owner", address=v.account.address)

    @router.callback_query()
    async def on_callback(callback: CallbackQuery, bot: Bot):
        if not seen.first_time(("c", callback.id)):
            return                                   # то же нажатие пришло второй раз
        v = visitor(callback.from_user)
        if v.kind not in ("owner", "user", "new"):
            await callback.answer("Доступ закрыт")
            return
        if v.kind != "owner" and not limiter.allow(callback.from_user.id)[0]:
            await callback.answer("Не так быстро 🙂")
            return
        async with access.lock(v.account.id):        # нажатия — по очереди с сообщениями этого человека
            await handle_callback(callback, bot, v)

    async def handle_callback(callback: CallbackQuery, bot: Bot, v: Visitor):
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
        if data in ("me:delete", "me:keep") and not owner:
            await callback.answer()
            msg = callback.message
            if data == "me:keep":
                if msg:
                    await msg.edit_text(personalize(Reply("🎩 Как скажете, Сэр! Всё остаётся на месте."),
                                                    v.account.address).text)
                return
            label = v.account.label
            access.db.delete_user(v.account.id)
            access.forget(v.account.id)
            if msg:
                await msg.edit_text(personalize(Reply(T.DELETE_ME_DONE), v.account.address).text)
            try:
                await bot.send_message(access.owner_tg, personalize(
                    Reply(f"🎩 Сэр, {label} удалил свой аккаунт у Альфреда."), access.owner.address).text)
            except Exception:
                log.exception("Не удалось уведомить владельца")
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
        if data.startswith("fwd:"):
            await callback.answer()
            channel, text = forwards.pop(v.account.id, (None, None)) if owner else (None, None)
            msg = callback.message
            if data == "fwd:text" and text:
                if msg:
                    try:
                        await msg.edit_reply_markup(reply_markup=None)
                    except TelegramBadRequest:
                        pass
                await handle_text(text, callback.from_user.id, bot, v)
                return
            if data == "fwd:backup" and channel and backup:
                result = await backup.connect(channel)
            elif data == "fwd:no" and channel:
                result = Reply("🎩 Как скажете, Сэр! Ничего не подключаю.")
            else:
                result = Reply("🎩 Сэр, эта кнопка уже неактуальна!")
            result = personalize(result, v.account.address)
            try:
                if msg:
                    await msg.edit_text(result.text)
                    return
            except TelegramBadRequest:
                pass
            await send_reply(bot, callback.from_user.id, result, owner=owner, address=v.account.address)
            return
        if data.startswith("bk:"):
            if not owner or not backup:
                await callback.answer("Доступ закрыт")
                return
            await callback.answer("Минутку…" if data in ("bk:now", "bk:yes") or data.startswith("bk:rest") else None)
            reply = personalize(await backup.callback(data, callback.from_user.id), v.account.address)
            try:
                if reply.edit and callback.message:
                    await callback.message.edit_text(reply.text, reply_markup=inline(reply.buttons))
                    return
            except TelegramBadRequest:
                pass
            if reply.toast:
                return
            await send_reply(bot, callback.from_user.id, reply, owner=True, address=v.account.address)
            return
        if data.startswith("adm:"):
            if not owner:
                await callback.answer("Доступ закрыт")
                return
            if data == "adm:selftest":
                await callback.answer("Запускаю самопроверку…")
                start_selftest(bot, v)
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
            if data == "confirm:reset_all" and owner and access.university and reply.text.startswith(T.RESET_DONE):
                restore_lessons(bot, v)               # «Чистый лист» стёр и пары — сразу подгружаем их заново
        reply = personalize(reply, v.account.address)
        await callback.answer(reply.toast or None)
        msg = callback.message
        fits = len(reply.text) <= TEXT_LIMIT     # слишком длинное не влезет в старое сообщение — пришлём новыми
        try:
            if reply.edit and msg and fits:
                await msg.edit_text(reply.text, reply_markup=inline(reply.buttons))
                return
            if (reply.clear_source_buttons or reply.edit) and msg:
                await msg.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest as exc:
            # Например, «сообщение не изменилось» — это не ошибка для пользователя.
            log.info("Edit skipped: %s", exc)
            if reply.edit and fits:
                return
        await send_reply(bot, callback.from_user.id, reply, owner=owner, address=v.account.address)

    @router.errors()
    async def on_error(event: ErrorEvent, bot: Bot):
        """Непредвиденная ошибка: записываем в журнал и вежливо говорим человеку, а не молчим."""
        log.error("Ошибка при обработке сообщения", exc_info=event.exception)
        update = event.update
        if update.callback_query:
            chat_id = update.callback_query.from_user.id
            try:
                await update.callback_query.answer()         # убираем «часики» с кнопки
            except Exception:
                pass
        elif update.message and update.message.from_user:
            chat_id = update.message.from_user.id
        else:
            return
        try:
            acc = access.users.by_telegram(chat_id)
            if acc and acc.status == "active":               # чужим и закрытым — молчим, как и раньше
                await bot.send_message(chat_id, personalize(Reply(T.SOMETHING_WRONG), acc.address).text)
        except Exception:
            log.exception("Не удалось сообщить об ошибке")

    return router


def build_dispatcher(access: Access, admin: Admin, backup=None) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router(access, admin, backup))
    return dp
