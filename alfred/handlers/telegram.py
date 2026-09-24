"""Связь Telegram ↔ ядро Альфреда. Здесь нет бизнес-логики — только отправка сообщений."""

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from ..core.alfred import Alfred
from ..core.reply import Reply
from ..ui.keyboards import inline, main_menu

log = logging.getLogger(__name__)


async def send_reply(bot: Bot, chat_id: int, reply: Reply) -> None:
    markup = inline(reply.buttons) or main_menu()
    await bot.send_message(chat_id, reply.text, reply_markup=markup)
    for extra in reply.extra:
        await bot.send_message(chat_id, extra.text, reply_markup=inline(extra.buttons) or main_menu())


def build_router(alfred: Alfred, owner_id: int) -> Router:
    router = Router()
    # Бот приватный: чужие сообщения и нажатия просто игнорируются.
    router.message.filter(F.from_user.id == owner_id)
    router.callback_query.filter(F.from_user.id == owner_id)

    @router.message(CommandStart())
    async def on_start(message: Message):
        reply = alfred.start()
        await message.answer(reply.text, reply_markup=main_menu())

    @router.message(Command("reset", "clean"))
    async def on_reset(message: Message):
        reply = alfred.reset_request()
        await message.answer(reply.text, reply_markup=inline(reply.buttons))

    @router.message(F.text)
    async def on_text(message: Message, bot: Bot):
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await alfred.handle_text(message.text)
        await send_reply(bot, message.chat.id, reply)

    @router.message()
    async def on_other(message: Message):
        await message.answer("🎩 Сэр, пока я понимаю только текст!", reply_markup=main_menu())

    @router.callback_query()
    async def on_callback(callback: CallbackQuery, bot: Bot):
        reply = await alfred.handle_callback_async(callback.data or "")
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
        await send_reply(bot, callback.from_user.id, reply)

    return router


def build_dispatcher(alfred: Alfred, owner_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router(alfred, owner_id))
    return dp
