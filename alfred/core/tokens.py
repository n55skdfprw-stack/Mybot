"""🧮 Расход токенов GigaChat — по дням и месяцам, отдельно Lite и Pro (видно в ⚙️ Система).

Бесплатный лимит физлица (Freemium) — на 12 месяцев: Lite 250 млн, Pro 40 млн токенов (данные Сбера на октябрь 2026).
"""

from datetime import datetime
from typing import Callable, Optional

from ..database.repositories import UserRepository

YEAR_LIMIT = {"Lite": 250_000_000, "Pro": 40_000_000}
MODELS = ("Lite", "Pro")


class TokenMeter:
    def __init__(self, users: UserRepository, clock: Callable[[], datetime]):
        self.users = users
        self.clock = clock

    def add(self, model: str, tokens: int) -> None:
        now = self.clock()
        for key in (f"tokens:day:{now:%Y-%m-%d}:{model}", f"tokens:month:{now:%Y-%m}:{model}",
                    f"tokens:total:{model}"):
            self.users.set_setting(key, str(self._get(key) + tokens))

    def _get(self, key: str) -> int:
        try:
            return int(self.users.setting(key) or 0)
        except ValueError:
            return 0

    def today(self, model: str) -> int:
        return self._get(f"tokens:day:{self.clock():%Y-%m-%d}:{model}")

    def month(self, model: str) -> int:
        return self._get(f"tokens:month:{self.clock():%Y-%m}:{model}")

    def lines(self) -> str:
        """Строки для экрана ⚙️ Система."""
        def n(x: int) -> str:
            if x >= 1_000_000:
                return f"{x / 1_000_000:.1f} млн".replace(".", ",")
            return f"{round(x / 1000)} тыс" if x >= 1000 else str(x)

        today = " · ".join(f"{m} {n(self.today(m))}" for m in MODELS)
        month = " · ".join(f"{m} {n(self.month(m))}" for m in MODELS)
        days = self.clock().day                       # сколько дней месяца прошло — средний расход в день
        share = " · ".join(f"{m} {self.month(m) / days * 365 / YEAR_LIMIT[m]:.0%}" for m in MODELS)
        return (f"🧮 Токены GigaChat сегодня: {today}\n"
                f"🧮 За месяц: {month}\n"
                f"📈 Если весь год так же — уйдёт от годового лимита: {share}")


def meter_for(users: UserRepository, clock: Optional[Callable[[], datetime]], tz) -> TokenMeter:
    return TokenMeter(users, clock or (lambda: datetime.now(tz)))
