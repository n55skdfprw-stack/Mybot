"""Единая точка обращения к ИИ. Чтобы сменить провайдера — достаточно заменить этот модуль."""

import logging
from typing import Protocol

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
            return response.choices[0].message.content or ""
        except Exception as exc:  # сеть, авторизация, лимиты
            log.exception("GigaChat error (model=%s)", model)
            raise LLMError(str(exc)) from exc

    async def check(self) -> bool:
        """Проверка доступности сервиса при запуске."""
        try:
            await self.complete("Ответь одним словом.", "Скажи: работает", pro=False)
            return True
        except LLMError:
            return False
