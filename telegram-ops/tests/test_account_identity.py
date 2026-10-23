"""Names are display-only; mocked Telegram, temporary database, no sends."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from telethon.errors import FloodWaitError

from app import account_identity as identity, relay_engine, workers
from app.database import session_scope
from app.models import Account
from app.relay_models import ConsoleState, RelayTask
from app.main import app
from test_console_api import login, HEAD
from test_relay import seed
from test_runtime_lifecycle import clients


def profile():
    return NS(id=111, first_name="测试", last_name="账号", username="example_name")


def setup_account():
    seed()
    with session_scope() as db:
        db.get(Account, 1).session_string_encrypted = "fixture-session"


def client(user=None):
    return NS(get_me=AsyncMock(return_value=user or profile()), is_connected=lambda: True,
              connect=AsyncMock(), disconnect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True),
              send_message=AsyncMock())


def test_sync_uses_live_client_updates_api_and_preserves_labels_and_bindings(monkeypatch):
    with TestClient(app) as http:
        setup_account()
        c = client()
        monkeypatch.setattr(relay_engine.manager, "workers", {1: NS(client=c)})
        login(http)
        response = http.post("/api/accounts/1/identity/sync", headers=HEAD)
        assert response.status_code == 200
        assert response.json()["name"] == "测试 账号"
        assert response.json()["username"] == "example_name"
        assert response.json()["label"] == "Account 1"
        state = http.get("/api/state").json()
        a = state["accounts"][0]
        assert a["name"] == "测试 账号" and a["telegram_user_id"] == 111 and a["identity_synced_at"]
        assert a["monitor_chat_ids"] == [-1001]
        assert "fixture-session" not in response.text + str(state)
        with session_scope() as db:
            assert db.get(Account, 1).name == "Account 1"
            assert db.get(RelayTask, 1).enabled
            assert db.get(Account, 1).session_string_encrypted == "fixture-session"
        c.connect.assert_not_awaited()
        c.disconnect.assert_not_awaited()
        c.send_message.assert_not_awaited()
        # Removing a Telegram username clears the previous cached username.
        c.get_me.return_value = NS(id=111, first_name="新名称", last_name=None, username=None)
        updated = http.post("/api/accounts/1/identity/sync", headers=HEAD).json()
        assert updated["name"] == "新名称" and updated["username"] is None


@pytest.mark.parametrize("failure", [TimeoutError("private-secret"), ValueError("private-secret")])
def test_failed_sync_preserves_previous_data_and_redacts_error(monkeypatch, failure):
    with TestClient(app) as http:
        setup_account()
        with session_scope() as db:
            original = identity.save_identity(db, 1, profile())
        c = client()
        c.get_me.side_effect = failure
        monkeypatch.setattr(relay_engine.manager, "workers", {1: NS(client=c)})
        login(http)
        r = http.post("/api/accounts/1/identity/sync", headers=HEAD)
        assert r.status_code == 400 and "private-secret" not in r.text
        with session_scope() as db:
            assert identity.identities_for(db, [1])[1] == original
        c.disconnect.assert_not_awaited()


def test_stopped_service_sync_uses_short_lived_client_and_does_not_start(monkeypatch):
    setup_account()
    c = client()
    monkeypatch.setattr(identity, "make_client", lambda *a: c)
    monkeypatch.setattr(identity, "decrypt_text", lambda value: "fake")
    assert asyncio.run(identity.refresh_identity(1))["username"] == "example_name"
    c.connect.assert_awaited_once()
    c.disconnect.assert_awaited_once()
    c.send_message.assert_not_awaited()


def test_stale_session_result_is_not_saved():
    setup_account()
    c = client()
    async def changed():
        with session_scope() as db:
            db.get(Account, 1).session_string_encrypted = None
        return profile()
    c.get_me.side_effect = changed
    with pytest.raises(identity.IdentitySyncError):
        asyncio.run(identity.refresh_identity(1, c))
    with session_scope() as db:
        assert identity.identities_for(db, [1]) == {}


def test_flood_wait_is_saved_and_repeated_sync_does_not_call_telegram():
    setup_account()
    c = client()
    c.get_me.side_effect = FloodWaitError(None, capture=300)
    with pytest.raises(FloodWaitError):
        asyncio.run(identity.refresh_identity(1, c))
    with pytest.raises(identity.IdentitySyncError, match="等待"):
        asyncio.run(identity.refresh_identity(1, c))
    assert c.get_me.await_count == 1
    with session_scope() as db:
        assert db.get(Account, 1).flood_wait_until and db.get(Account, 1).status == "flood_wait"


def test_identity_api_requires_auth_and_csrf():
    with TestClient(app) as http:
        assert http.post("/api/accounts/1/identity/sync", headers=HEAD).status_code == 401
        login(http)
        assert http.post("/api/accounts/1/identity/sync").status_code == 403
        assert http.post("/api/accounts/999/identity/sync", headers=HEAD).status_code == 404


def test_login_persists_identity_even_when_service_is_paused(monkeypatch):
    setup_account()
    c = client()
    c.sign_in = AsyncMock(return_value=profile())
    c.session = object()
    monkeypatch.setattr(workers, "make_client", lambda *a, **kw: c)
    monkeypatch.setattr(workers, "decrypt_text", lambda value: "fake")
    monkeypatch.setattr(workers.StringSession, "save", lambda session: "session")
    asyncio.run(workers.verify_login_code(1, "dummy-code"))
    with session_scope() as db:
        assert identity.identities_for(db, [1])[1]["username"] == "example_name"
        assert db.get(Account, 1).status == "active"


def test_reconnect_refreshes_identity_without_changing_local_label(clients, monkeypatch):
    client_type, _ = clients
    monkeypatch.setattr(client_type, "get_me", AsyncMock(return_value=profile()))
    async def run():
        e = relay_engine.RelayEngine()
        e.running = True
        await e.reload_account(1)
        assert e.identities[1] == 111
        with session_scope() as db:
            assert identity.identities_for(db, [1])[1]["username"] == "example_name"
            assert db.get(Account, 1).name == "Account 1"
        await e.stop()
    asyncio.run(run())


def test_busy_account_returns_promptly_without_requesting_telegram(monkeypatch):
    from app.console_api import sync_identity
    from fastapi import HTTPException
    setup_account()
    monkeypatch.setattr(relay_engine.manager, "account_locks", {})
    async def run():
        async with relay_engine.manager.account_lock(1):
            with session_scope() as db, pytest.raises(HTTPException) as error:
                await asyncio.wait_for(sync_identity(1, db), .1)
            assert error.value.status_code == 409
    asyncio.run(run())


def test_temporary_client_timeout_closes_connection_and_preserves_cache(monkeypatch):
    setup_account()
    c = client()
    monkeypatch.setattr(identity, "make_client", lambda *a: c)
    monkeypatch.setattr(identity, "decrypt_text", lambda value: "fake")
    monkeypatch.setattr(identity, "CONNECT_TIMEOUT_SECONDS", .01)
    async def wait():
        await asyncio.Future()
    c.get_me.side_effect = wait
    with pytest.raises(TimeoutError):
        asyncio.run(identity.refresh_identity(1))
    c.disconnect.assert_awaited_once()
    with session_scope() as db:
        assert identity.identities_for(db, [1]) == {}
