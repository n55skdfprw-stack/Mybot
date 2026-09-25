"""Защита от спама и перегрузки: сколько сообщений можно присылать и кому Альфред вообще отвечает."""

import time
from collections import defaultdict, deque
from typing import Callable

MAX_TEXT = 1500                 # длиннее — просим разбить: так дешевле для ИИ и надёжнее
PER_MINUTE = 20                 # сообщений и нажатий в минуту от одного человека
PER_BURST = 4                   # подряд за 3 секунды
STRANGER_REPLY_EVERY = 600      # чужим и закрытым — отвечаем не чаще раза в 10 минут
PASTE_WINDOW = 10               # Telegram режет огромный текст на куски — все куски одной вставки пропускаем


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._hits: dict[int, deque] = defaultdict(deque)
        self._warned: dict[int, float] = {}
        self._stranger: dict[int, float] = {}
        self._too_long: dict[int, float] = {}

    def too_long(self, telegram_id: int, length: int, limit: int) -> tuple[bool, bool]:
        """(пропустить ли сообщение, ответить ли «слишком длинное»).

        Очень длинный текст Telegram присылает несколькими сообщениями подряд. Отвечаем один раз,
        а остальные куски этой же вставки (и короткий хвостик тоже) молча пропускаем."""
        now = self.clock()
        recent = now - self._too_long.get(telegram_id, -1e9) <= PASTE_WINDOW
        if length > limit:
            self._too_long[telegram_id] = now
            return True, not recent
        if recent:
            self._too_long[telegram_id] = now
            return True, False
        return False, False

    def allow(self, telegram_id: int) -> tuple[bool, bool]:
        """(можно ли обработать, надо ли один раз предупредить «не так быстро»)."""
        now = self.clock()
        hits = self._hits[telegram_id]
        while hits and now - hits[0] > 60:
            hits.popleft()
        burst = sum(1 for t in hits if now - t <= 3)
        if len(hits) >= PER_MINUTE or burst >= PER_BURST:
            warn = now - self._warned.get(telegram_id, -1e9) > 60
            if warn:
                self._warned[telegram_id] = now
            return False, warn
        hits.append(now)
        return True, False

    def stranger_may_get_reply(self, telegram_id: int) -> bool:
        """Незнакомцам и закрытым — вежливый отказ, но не на каждое сообщение (иначе бота можно заспамить)."""
        now = self.clock()
        if now - self._stranger.get(telegram_id, -1e9) < STRANGER_REPLY_EVERY:
            return False
        self._stranger[telegram_id] = now
        if len(self._stranger) > 5000:                  # не копим память бесконечно
            self._stranger = {k: v for k, v in self._stranger.items() if now - v < STRANGER_REPLY_EVERY}
        return True
