"""Клавиатуры Telegram."""

from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from ..core.reply import Buttons
from . import texts as T


def main_menu(owner: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=T.MENU_TASKS), KeyboardButton(text=T.MENU_SCHEDULE)],
        [KeyboardButton(text=T.MENU_NOTES), KeyboardButton(text=T.MENU_FINANCE)],
        [KeyboardButton(text=T.MENU_BIRTHDAYS), KeyboardButton(text=T.MENU_DOSSIER)],
        [KeyboardButton(text=T.MENU_WEATHER), KeyboardButton(text=T.MENU_MED)],
    ]
    if owner:
        rows.append([KeyboardButton(text=T.MENU_USERS), KeyboardButton(text=T.MENU_SYSTEM)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напишите Альфреду…",
    )


def inline(buttons: Optional[Buttons]) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in buttons
    ])
