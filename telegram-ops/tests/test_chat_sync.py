"""Bulk synchronization preserves metadata and listener selection."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app import workers
from app.database import engine, session_scope
from app.models import Account, Chat
from test_relay import seed


@pytest.mark.parametrize("count", [2, 200])
def test_sync_bounded_reads_and_preserves_disabled_and_missing_chats(monkeypatch, count):
    seed()
    with session_scope() as db:
        db.query(Chat).delete()
        db.get(Account, 1).status = "active"
        db.get(Account, 2).status = "active"
        db.add_all([
            Chat(account_id=2, telegram_chat_id=-11, title="other", type="group"),
            Chat(account_id=1, telegram_chat_id=-12, title="keep title", type="group",
                 enabled=False, is_primary_listener=False),
            Chat(account_id=1, telegram_chat_id=-99, title="missing", type="group"),
        ])
    dialogs = [SimpleNamespace(id=-11-i, name="new" if i != 1 else "",
                               entity=SimpleNamespace(megagroup=True)) for i in range(count)]
    # Repeated SDK entries must not create duplicate account/group rows.
    dialogs += [dialogs[0], SimpleNamespace(id=42, name="person", entity=SimpleNamespace())]

    class Client:
        closed = False
        async def connect(self): pass
        async def is_user_authorized(self): return True
        async def get_dialogs(self): return dialogs
        async def disconnect(self): self.closed = True

    client = Client()
    monkeypatch.setattr(workers, "make_client", lambda *a, **kw: client)
    monkeypatch.setattr(workers, "decrypt_text", lambda value: "fake")
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert asyncio.run(workers.sync_account_chats(1)) == count + 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert client.closed
    assert len(statements) == 3  # account, existing chats, global listener selection
    with session_scope() as db:
        rows = db.query(Chat).filter(Chat.telegram_chat_id == -11).order_by(Chat.id).all()
        assert [r.is_primary_listener for r in rows] == [True, False]
        disabled = db.query(Chat).filter_by(account_id=1, telegram_chat_id=-12).one()
        assert (disabled.title, disabled.enabled, disabled.is_primary_listener) == ("keep title", False, False)
        assert disabled.type == "supergroup" and disabled.last_sync_at
        assert db.query(Chat).filter_by(account_id=1, telegram_chat_id=-99).count() == 1
        assert db.query(Chat).filter_by(telegram_chat_id=42).count() == 0


def test_listener_active_account_preferred_and_disabled_rows_untouched():
    seed()
    with session_scope() as db:
        db.query(Chat).delete()
        db.get(Account, 1).status = "disabled"
        db.get(Account, 2).status = "active"
        db.add_all([
            Chat(account_id=1, telegram_chat_id=-1, title="old", type="group"),
            Chat(account_id=2, telegram_chat_id=-1, title="new", type="group"),
            Chat(account_id=1, telegram_chat_id=-2, title="disabled", type="group", enabled=False),
        ])
        workers._assign_primary_listeners(db)
    with session_scope() as db:
        rows = db.query(Chat).order_by(Chat.id).all()
        assert [r.is_primary_listener for r in rows] == [False, True, True]


def test_legacy_logs_do_not_include_message_text(monkeypatch, caplog):
    worker = workers.TelegramWorker(1)
    event = SimpleNamespace(raw_text="private-body-must-not-be-logged", chat_id=-1,
                            message=SimpleNamespace(id=7))
    async def sender(): return SimpleNamespace(id=42, username="example")
    event.get_sender = sender
    with caplog.at_level("DEBUG", logger="app.workers"):
        asyncio.run(worker._handle_message(event))
    assert "private-body-must-not-be-logged" not in caplog.text
    assert "account=1 chat=-1 message=7" in caplog.text
