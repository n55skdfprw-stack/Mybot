"""📁 Резерв: каждую ночь тихо кладёт зашифрованную копию базы в закрытый канал владельца,
хранит 2 последние, по кнопке присылает копию или восстанавливает из неё."""

import json
import logging
from datetime import datetime
from typing import Optional

from ..services.backup import ERRORS, KEEP, BackupError, make_backup, read_backup, restore_backup
from ..ui import texts as T
from .access import Access
from .reply import Reply

log = logging.getLogger(__name__)

SETUP = ("📁 Как подключить папку для копий (один раз):\n"
         "1. Telegram → новый канал, например «Альфред · Резерв», тип — Частный.\n"
         "2. Настройки канала → Администраторы → добавьте Альфреда (права: публиковать и удалять сообщения).\n"
         "3. Перешлите сюда любое сообщение из этого канала — я подключу его сам.")


class BackupManager:
    def __init__(self, bot, access: Access):
        self.bot = bot
        self.access = access
        self.users = access.users
        self._restore: Optional[tuple[bytes, str]] = None      # копия, ждущая подтверждения

    # ------------------------------------------------------------ настройки
    @property
    def channel(self) -> Optional[int]:
        v = self.users.setting("backup_channel")
        return int(v) if v else None

    def copies(self) -> list[dict]:
        try:
            return json.loads(self.users.setting("backup_copies") or "[]")
        except ValueError:
            return []

    def _save_copies(self, items: list[dict]) -> None:
        self.users.set_setting("backup_copies", json.dumps(items))

    def _now(self) -> datetime:
        return self.access.clock() if self.access.clock else datetime.now(self.access.tz)

    @staticmethod
    def _when(iso: str) -> str:
        dt = datetime.fromisoformat(iso)
        return f"{dt.day} {T.MONTHS_GEN[dt.month - 1][:3]} в {dt:%H:%M}"

    def status_line(self) -> str:
        if not self.channel:
            return "💾 Резерв: канал не подключён"
        items = self.copies()
        err = self.users.setting("backup_error")
        if err:
            return f"💾 Резерв: ⚠️ последняя копия не удалась"
        return f"💾 Резерв: последняя копия {self._when(items[-1]['at'])}" if items else "💾 Резерв: копий пока нет"

    # ------------------------------------------------------------ копия
    async def run(self) -> Optional[str]:
        """Сделать копию и тихо положить в канал. Возвращает текст ошибки или None."""
        if not self.channel:
            return "канал не подключён"
        from aiogram.types import BufferedInputFile
        try:
            snap = make_backup(self.access.db, self._now())
            size = len(snap.data)
            msg = await self.bot.send_document(
                self.channel, BufferedInputFile(snap.data, snap.filename),
                caption=f"💾 {snap.filename}\n{snap.summary}", disable_notification=True)
        except BackupError as e:
            self.users.set_setting("backup_error", str(e))
            return ERRORS.get(str(e), str(e))
        except Exception as e:
            log.exception("Backup failed")
            self.users.set_setting("backup_error", "send")
            return f"не получилось отправить в канал ({type(e).__name__}) — проверьте, что Альфред там администратор"
        items = self.copies() + [{"msg": msg.message_id, "file": msg.document.file_id,
                                  "at": self._now().isoformat(timespec="minutes"), "size": size}]
        while len(items) > KEEP:                       # пришла 3-я — удаляем 1-ю
            old = items.pop(0)
            try:
                await self.bot.delete_message(self.channel, old["msg"])
            except Exception:
                log.warning("Не удалось удалить старую копию %s", old.get("msg"))
        self._save_copies(items)
        self.users.set_setting("backup_error", None)
        return None

    async def connect(self, chat_id: int) -> Reply:
        """Подключить канал: проверяем, что можем туда писать и удалять, и сразу делаем первую копию."""
        try:
            probe = await self.bot.send_message(chat_id, "✅ Канал подключён к Альфреду", disable_notification=True)
            await self.bot.delete_message(chat_id, probe.message_id)
        except Exception:
            return Reply("🎩 Сэр, я не могу писать или удалять сообщения в этом канале!\n\n"
                         "Добавьте меня в администраторы канала с правами «публиковать» и «удалять сообщения».")
        self.users.set_setting("backup_channel", str(chat_id))
        self._save_copies([])
        err = await self.run()
        if err:
            return Reply(f"🎩 Канал подключён, Сэр, но копию сделать не удалось!\n\n⚠️ {err}")
        return Reply("🎩 Готово, Сэр! Канал для резервных копий подключён!\n\n"
                     "💾 Первая копия уже там. Дальше — каждую ночь в 4:00, тихо, храню 2 последние.")

    # ------------------------------------------------------------ экран 📁 Резерв
    def view(self, edit: bool = True) -> Reply:
        if not self.access.db.cipher.on:
            return Reply("🎩 Резерв, Сэр!\n\n⚠️ " + ERRORS["no_key"], buttons=[[("↩️ Назад", "adm:sys")]], edit=edit)
        if not self.channel:
            return Reply(f"🎩 Резерв, Сэр!\n\n{SETUP}", buttons=[[("↩️ Назад", "adm:sys")]], edit=edit)
        items = self.copies()
        newest_first = list(reversed(list(enumerate(items))))
        lines = [f"💾 №{n} · {self._when(c['at'])} · {c['size'] / 1024 / 1024:.1f} МБ".replace(".", ",")
                 + (" (свежая)" if n == 1 else "")
                 for n, (_, c) in enumerate(newest_first, 1)] or ["Копий пока нет"]
        err = self.users.setting("backup_error")
        warn = f"\n\n⚠️ Последняя попытка не удалась: {ERRORS.get(err, 'не получилось отправить в канал')}" \
            if err else ""
        rows = [[("💾 Сделать копию сейчас", "bk:now")]]
        for n, (i, c) in enumerate(newest_first, 1):
            rows.append([(f"📥 Прислать №{n}", f"bk:get:{i}"), (f"♻️ Восстановить №{n}", f"bk:rest:{i}")])
        rows.append([("↩️ Назад", "adm:sys")])
        return Reply("🎩 Резерв, Сэр!\n\nКаждую ночь в 4:00 кладу зашифрованную копию в ваш канал и храню 2 "
                     "последние.\n\n" + "\n".join(lines) + warn, buttons=rows, edit=edit)

    # ------------------------------------------------------------ восстановление
    async def _download(self, file_id: str) -> bytes:
        buf = await self.bot.download(file_id)
        return buf.read()

    def _ask_restore(self, data: bytes, label: str) -> Reply:
        try:
            raw, info = read_backup(self.access.db, data)
        except BackupError as e:
            self._restore = None
            return Reply(f"🎩 Сэр, восстановить нельзя!\n\n⚠️ {ERRORS.get(str(e), str(e))}")
        self._restore = (raw, label)
        return Reply(f"🎩 Сэр, восстановить базу из копии {label}?\n\nВ копии: {info}\n\n"
                     "⚠️ Всё, что записано ПОСЛЕ этой копии (у вас и у гостей), пропадёт. Отменить будет нельзя.",
                     buttons=[[("♻️ Да, восстановить", "bk:yes")], [("↩️ Нет", "bk:no")]])

    async def ask_restore_upload(self, file_id: str, name: str) -> Reply:
        return self._ask_restore(await self._download(file_id), f"«{name}»")

    async def callback(self, data: str, owner_chat: int) -> Reply:
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        num = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        items = self.copies()
        if action == "view":
            return self.view()
        if action == "now":
            err = await self.run()
            view = self.view()
            if err:
                view.text = f"⚠️ {err}\n\n" + view.text
            return view
        if action == "get" and num is not None and num < len(items):
            await self.bot.send_document(owner_chat, items[num]["file"],
                                         caption=f"💾 Копия от {self._when(items[num]['at'])}")
            return Reply("🎩 Прислал, Сэр!", toast="Копия отправлена")
        if action == "rest" and num is not None and num < len(items):
            return self._ask_restore(await self._download(items[num]["file"]),
                                     f"от {self._when(items[num]['at'])}")
        if action == "no":
            self._restore = None
            return Reply("🎩 Как скажете, Сэр! Ничего не меняю.", edit=True)
        if action == "yes" and self._restore:
            raw, label = self._restore
            self._restore = None
            restore_backup(self.access.db, raw)
            self.access._alfreds.clear()          # у всех — свежие данные из восстановленной базы
            self.access.setup_owner()
            return Reply(f"🎩 Готово, Сэр! База восстановлена из копии {label}!", edit=True)
        return Reply("🎩 Сэр, эта кнопка уже неактуальна!", clear_source_buttons=True)
