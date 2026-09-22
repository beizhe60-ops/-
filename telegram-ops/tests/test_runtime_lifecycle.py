"""Characterize resource cleanup without real Telegram sessions or sends."""
import asyncio
from types import SimpleNamespace

import pytest

from app import relay_engine
from app.database import session_scope
from app.models import Account
from app.relay_models import RelayTask
from test_relay import seed


@pytest.fixture
def clients(monkeypatch):
    seed()
    with session_scope() as db:
        for account in db.query(Account):
            account.session_string_encrypted = "fictional"
    made = []

    class Client:
        def __init__(self):
            self.connected = False
            self.handlers = []
            self.disconnects = 0
            made.append(self)

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.disconnects += 1
            self.connected = False

        def is_connected(self):
            return self.connected

        async def is_user_authorized(self):
            return True

        async def get_me(self):
            return SimpleNamespace(id=100 + len(made))

        async def get_dialogs(self):
            return []

        def add_event_handler(self, callback, event):
            self.handlers.append((callback, event))

    monkeypatch.setattr(relay_engine, "make_client", lambda *a, **kw: Client())
    monkeypatch.setattr(relay_engine, "decrypt_text", lambda value: value)
    return Client, made


def test_reload_and_stop_do_not_accumulate_connected_clients(clients):
    _, made = clients

    async def run():
        engine = relay_engine.RelayEngine()
        engine.running = True
        for revision in range(10):
            with session_scope() as db:
                db.get(RelayTask, 1).name = f"配置修改 {revision}"
            await engine.reload_workers()
        assert len(made) == 2
        assert len(engine.workers) == 2
        assert [len(client.handlers) for client in made] == [1, 1]
        with session_scope() as db:
            db.get(Account, 1).status = "disabled"
        await engine.reload_workers()
        assert set(engine.workers) == {2}
        assert made[0].disconnects == 1
        await engine.stop()
        await engine.stop()
        assert not engine.workers and not engine.identities and not engine.running
        assert all(not c.connected and c.disconnects == 1 for c in made)

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["connect", "get_dialogs"])
def test_initialization_failure_disconnects_client(clients, monkeypatch, stage):
    client_type, made = clients

    async def fail(self):
        self.connected = True
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(client_type, stage, fail)

    async def run():
        engine = relay_engine.RelayEngine()
        engine.running = True
        await engine.reload_workers()
        assert not engine.workers and not engine.identities
        assert all(not c.connected and c.disconnects == 1 and not c.handlers for c in made)
        with session_scope() as db:
            assert all(a.status == "connection_error" for a in db.query(Account))
        await engine.stop()

    asyncio.run(run())


def test_cancel_initialization_disconnects_client(clients, monkeypatch):
    client_type, made = clients

    async def run():
        entered = asyncio.Event()

        async def wait_for_network(self):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(client_type, "get_dialogs", wait_for_network)
        engine = relay_engine.RelayEngine()
        engine.running = True
        task = asyncio.create_task(engine.reload_workers())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not engine.workers and not engine.identities
        assert len(made) == 2
        assert all(c.disconnects == 1 and not c.connected for c in made)
        await engine.stop()

    asyncio.run(run())


def test_repeated_start_stop_awaits_the_main_loop(monkeypatch):
    async def run():
        engine = relay_engine.RelayEngine()
        entered = asyncio.Event()

        async def no_network(**kwargs):
            entered.set()

        async def no_send(**kwargs):
            pass

        monkeypatch.setattr(engine, "reload_workers", no_network)
        monkeypatch.setattr(engine, "process_pending_queue", no_send)
        previous = None
        for _ in range(3):
            entered.clear()
            await engine.start()
            task = engine.loop_task
            assert task is not previous
            await engine.start()
            assert engine.loop_task is task
            await asyncio.wait_for(entered.wait(), timeout=1)
            await engine.stop()
            assert task.done() and not engine.running
            previous = task

    asyncio.run(run())


def test_slow_connection_does_not_block_another_account(clients, monkeypatch):
    client_type, made = clients
    def make(account, *args):
        client = client_type(); client.account_id = account.id; return client
    monkeypatch.setattr(relay_engine, "make_client", make)
    async def run():
        entered, release, other_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def dialogs(self):
            if self.account_id == 1:
                entered.set(); await release.wait()
            else:
                other_ready.set()
            return []
        monkeypatch.setattr(client_type, "get_dialogs", dialogs)
        engine = relay_engine.RelayEngine(); engine.running = True
        await engine.reload_workers(wait=False)
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(other_ready.wait(), 1)
        assert 2 in engine.workers and 1 not in engine.workers
        async with asyncio.timeout(1):
            async with engine.lock:
                pass
        with session_scope() as db:
            db.get(Account, 1).status = "disabled"
        release.set()
        await asyncio.gather(*list(engine.connection_tasks.values()))
        assert 1 not in engine.workers
        assert made[0].disconnects == 1
        await engine.stop()
    asyncio.run(run())


def test_full_handshake_timeout_cleans_client(clients, monkeypatch):
    client_type, made = clients
    monkeypatch.setattr(relay_engine, "CONNECT_TIMEOUT_SECONDS", 0.01)
    async def never(self):
        await asyncio.Future()
    monkeypatch.setattr(client_type, "get_dialogs", never)
    async def run():
        engine = relay_engine.RelayEngine(); engine.running = True
        await engine.reload_workers()
        assert not engine.workers and not engine.identities
        assert all(not c.connected and c.disconnects == 1 for c in made)
        with session_scope() as db:
            assert all(a.status == "connection_error" for a in db.query(Account))
        await engine.stop()
    asyncio.run(run())


def test_connections_are_bounded_and_continue_after_first_batch(clients, monkeypatch):
    client_type, made = clients
    with session_scope() as db:
        for aid in range(3, 7):
            db.add(Account(id=aid, name=f"Account {aid}", phone=f"+12025550{aid:03}",
                api_id=1, api_hash_encrypted="fictional", session_string_encrypted="fictional", status="active"))
    async def run():
        release = asyncio.Event()
        async def dialogs(self):
            await release.wait(); return []
        monkeypatch.setattr(client_type, "get_dialogs", dialogs)
        engine = relay_engine.RelayEngine(); engine.running = True
        await engine.reload_workers(wait=False)
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(made) == len(engine.connection_tasks) == 4
        await engine.reload_workers(wait=False)
        assert len(engine.connection_tasks) == 4
        release.set()
        await asyncio.gather(*list(engine.connection_tasks.values()))
        await engine.reload_workers()
        assert len(engine.workers) == 6
        await engine.stop()
        assert all(c.disconnects == 1 and not c.connected for c in made)
    asyncio.run(run())


def test_disconnect_timeout_is_observable_and_cancels_wait(monkeypatch, caplog):
    monkeypatch.setattr(relay_engine, "DISCONNECT_TIMEOUT_SECONDS", 0.01)
    async def run():
        cancelled = asyncio.Event()
        async def disconnect():
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        await relay_engine.RelayEngine.close_client(SimpleNamespace(disconnect=disconnect))
        assert cancelled.is_set()
    asyncio.run(run())
    assert "disconnect timed out" in caplog.text
