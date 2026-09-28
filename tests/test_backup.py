"""Тесты резервных копий: ночная копия в канал, хранятся 2 последние, прислать и восстановить."""

import asyncio
import io
from types import SimpleNamespace

from alfred.core.backup_manager import BackupManager
from alfred.database.crypto import new_key
from alfred.database.db import Database

from .test_access import make as make_access, run

CHANNEL = -100500


class FakeBot:
    def __init__(self):
        self.files, self.deleted, self.sent, self.next_id = {}, [], [], 1
        self.fail_channel = False

    async def send_message(self, chat_id, text, **kw):
        if self.fail_channel and chat_id == CHANNEL:
            raise RuntimeError("not admin")
        self.next_id += 1
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=self.next_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    async def send_document(self, chat_id, document, caption=None, **kw):
        self.next_id += 1
        if isinstance(document, str):
            self.sent.append((chat_id, caption))
            return SimpleNamespace(message_id=self.next_id)
        fid = f"file{self.next_id}"
        self.files[fid] = document.data
        self.sent.append((chat_id, caption))
        return SimpleNamespace(message_id=self.next_id, document=SimpleNamespace(file_id=fid))

    async def download(self, file_id):
        return io.BytesIO(self.files[file_id])


def make(tmp_path):
    access, admin, llm = make_access(tmp_path)
    access.db.cipher = Database(str(tmp_path / "k.db"), key=new_key()).cipher     # включаем шифрование
    bot = FakeBot()
    return access, admin, llm, bot, BackupManager(bot, access)


def test_connect_and_keep_two_latest(tmp_path):
    access, admin, llm, bot, bk = make(tmp_path)
    r = run(bk.connect(CHANNEL))
    assert r.text.startswith("🎩 Готово, Сэр! Канал для резервных копий подключён!")
    assert len(bk.copies()) == 1
    run(bk.run())
    run(bk.run())                                  # пришла 3-я — 1-я удалена
    assert len(bk.copies()) == 2 and len(bot.deleted) == 2       # 1 — проверочное сообщение, 1 — старая копия
    assert all(ch == CHANNEL for ch, _ in bot.sent if _ and "💾" in _)


def test_not_admin_in_channel(tmp_path):
    access, admin, llm, bot, bk = make(tmp_path)
    bot.fail_channel = True
    r = run(bk.connect(CHANNEL))
    assert "не могу писать" in r.text and bk.channel is None


def test_restore_from_copy(tmp_path):
    access, admin, llm, bot, bk = make(tmp_path)
    owner = access.alfred_for(access.owner)
    llm.said(intent="CREATE_NOTE", content="До копии")
    run(owner.handle_text("Запиши до копии"))
    run(bk.connect(CHANNEL))
    llm.said(intent="CREATE_NOTE", content="После копии")
    run(owner.handle_text("Запиши после копии"))
    r = run(bk.callback("bk:rest:0", 1000))
    assert r.text.startswith("🎩 Сэр, восстановить базу из копии") and "заметки 1" in r.text
    r = run(bk.callback("bk:yes", 1000))
    assert r.text.startswith("🎩 Готово, Сэр! База восстановлена")
    owner = access.alfred_for(access.owner)
    assert [n.content for n in owner.notes.all()] == ["До копии"]


def test_foreign_file_is_rejected(tmp_path):
    access, admin, llm, bot, bk = make(tmp_path)
    bot.files["x"] = b"hello"
    r = run(bk.ask_restore_upload("x", "x.bin"))
    assert "не резервная копия" in r.text


def test_system_view_has_backup(tmp_path):
    access, admin, llm, bot, bk = make(tmp_path)
    admin.backup = bk
    r = run(admin.system_view())
    assert "💾 Резерв: канал не подключён" in r.text and [("📁 Резерв", "bk:view")] in r.buttons
    assert "Как подключить папку" in bk.view().text
