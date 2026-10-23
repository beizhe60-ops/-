"""Lifecycle intent and queue integrity regressions; isolated DB, no network."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import relay_engine
from app.database import session_scope
from app.relay_models import ConsoleState, RelayJob, SenderBinding, SenderWeight
from test_console_api import login, HEAD
from test_relay import seed, engine, dm


@pytest.mark.parametrize("route", ["/workers/", "/api/service/"])
def test_both_service_routes_persist_intent_and_restart_stays_paused(route):
    with TestClient(app) as client:
        login(client)
        for action, value in [("start", "true"), ("start", "true"), ("stop", "false")]:
            response = client.post(route + action, headers=HEAD, follow_redirects=False)
            assert response.status_code == (303 if route == "/workers/" else 200)
            with session_scope() as db:
                assert db.get(ConsoleState, "running").value == value
            assert relay_engine.manager.running == (value == "true")
    with TestClient(app):
        assert not relay_engine.manager.running


def test_shutdown_preserves_enabled_intent_and_startup_restores_it():
    with TestClient(app) as client:
        login(client)
        assert client.post("/workers/start", headers=HEAD, follow_redirects=False).status_code == 303
    assert not relay_engine.manager.running
    with session_scope() as db:
        assert db.get(ConsoleState, "running").value == "true"
    with TestClient(app):
        assert relay_engine.manager.running


def test_racing_commands_serialize_intent_with_runtime():
    async def run():
        e = relay_engine.RelayEngine()
        e.running = True
        entered, release = asyncio.Event(), asyncio.Event()
        async def disconnect():
            entered.set()
            await release.wait()
        e.workers[1] = SimpleNamespace(client=SimpleNamespace(disconnect=disconnect))
        stopping = asyncio.create_task(e.stop(persist=True))
        await asyncio.wait_for(entered.wait(), 1)
        starting = asyncio.create_task(e.start(persist=True))
        await asyncio.sleep(0)
        with session_scope() as db:
            assert db.get(ConsoleState, "running").value == "false"
        release.set()
        await asyncio.gather(stopping, starting)
        with session_scope() as db:
            assert e.running and db.get(ConsoleState, "running").value == "true"
        await e.stop()
    asyncio.run(run())


def test_intent_write_failure_does_not_stop_running_service(monkeypatch):
    def broken(*args):
        raise RuntimeError("simulated DB unavailable")
    monkeypatch.setattr(relay_engine, "persist_running_intent", broken)
    async def run():
        e = relay_engine.RelayEngine()
        e.running = True
        with pytest.raises(RuntimeError):
            await e.stop(persist=True)
        assert e.running
    asyncio.run(run())


def test_duplicate_event_is_ignored_without_advancing_sender_weights():
    task = seed()
    e = engine()
    with session_scope() as db:
        db.add(SenderWeight(account_id=2, weight=1))
    dm(e, task)
    with session_scope() as db:
        scores = [(s.key, s.value) for s in db.query(ConsoleState)]
    dm(e, task)
    with session_scope() as db:
        assert db.query(RelayJob).count() == 1
        assert [(s.key, s.value) for s in db.query(ConsoleState)] == scores


@pytest.mark.parametrize("field", ["original", "text"])
def test_nonduplicate_integrity_error_is_observable_and_redacted(caplog, field):
    task = seed()
    e = engine()
    data = dict(task=task, stage="relay", account_id=1, chat_id=-1001, message_id=77,
                user_id=999, username="private-username", title="private-title",
                original="private-message", text="private-rendered-copy")
    data[field] = None
    with pytest.raises(relay_engine.EnqueueStorageError) as error:
        e.enqueue(**data)
    import traceback
    diagnostic = "".join(traceback.format_exception(error.value)) + caplog.text
    for value in ["private-username", "private-title", "private-message", "private-rendered-copy"]:
        assert value not in diagnostic
    assert "task=1 stage=relay account=1 chat=-1001 message=77" in caplog.text
    with session_scope() as db:
        assert db.query(RelayJob).count() == 0
    data[field] = "fixed"
    e.enqueue(**data)
    with session_scope() as db:
        assert db.query(RelayJob).count() == 1
