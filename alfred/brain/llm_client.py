"""Единая точка обращения к ИИ. Чтобы сменить провайдера — достаточно заменить этот модуль."""

import logging
from typing import Callable, Optional, Protocol

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    async def complete(self, system: str, user: str, pro: bool = False) -> str: ...


class GigaChatClient:
    def __init__(self, auth_key: str, model: str, model_pro: str):
        self.model = model
        self.model_pro = model_pro
        # Счётчик расхода: сюда сообщаем, сколько токенов ушло («Lite» или «Pro», число) — для ⚙️ Система
        self.on_usage: Optional[Callable[[str, int], None]] = None
        # verify_ssl_certs=False: у GigaChat сертификат российского УЦ (Минцифры),
        # которого нет в стандартном наборе сертификатов сервера.
        self.client = GigaChat(
            credentials=auth_key,
            scope="GIGACHAT_API_PERS",
            verify_ssl_certs=False,
            timeout=30,
        )

    async def complete(self, system: str, user: str, pro: bool = False) -> str:
        model = self.model_pro if pro else self.model
        payload = Chat(
            model=model,
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=system),
                Messages(role=MessagesRole.USER, content=user),
            ],
            temperature=0.1,
            max_tokens=600,
        )
        try:
            response = await self.client.achat(payload)
            text = response.choices[0].message.content or ""
        except Exception as exc:  # сеть, авторизация, лимиты
            log.exception("GigaChat error (model=%s)", model)
            raise LLMError(str(exc)) from exc
        spent = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
        if self.on_usage and spent:
            try:
                self.on_usage("Pro" if pro else "Lite", int(spent))
            except Exception:
                log.exception("Не удалось записать расход токенов")
        return text

    async def check(self) -> bool:
        """Проверка доступности сервиса при запуске."""
        try:
            await self.complete("Ответь одним словом.", "Скажи: работает", pro=False)
            return True
        except LLMError:
            return False
