"""Клавиатуры Telegram."""

from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from ..core.reply import Buttons
from . import texts as T


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.MENU_TASKS), KeyboardButton(text=T.MENU_SCHEDULE)],
            [KeyboardButton(text=T.MENU_NOTES), KeyboardButton(text=T.MENU_FINANCE)],
            [KeyboardButton(text=T.MENU_BIRTHDAYS), KeyboardButton(text=T.MENU_DOSSIER)],
            [KeyboardButton(text=T.MENU_WEATHER), KeyboardButton(text=T.MENU_MED)],
        ],
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
