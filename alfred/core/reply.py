"""Ответ Альфреда, не зависящий от Telegram (удобно для тестов)."""

from dataclasses import dataclass, field
from typing import Optional

Buttons = list[list[tuple[str, str]]]  # строки кнопок: (текст, callback_data)


@dataclass
class Reply:
    text: str
    buttons: Optional[Buttons] = None
    edit: bool = False                  # изменить сообщение, на котором нажали кнопку
    clear_source_buttons: bool = False  # убрать кнопки у исходного сообщения, а ответ прислать новым
    toast: Optional[str] = None         # короткое всплывающее уведомление при нажатии кнопки
    extra: list = field(default_factory=list)  # дополнительные сообщения после основного
