"""Login regression tests: real Telethon retry loop, simulated transport only."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import errors, types
from telethon.crypto import AuthKey

from app import workers
from app.console_api import login_error
from app.crypto import decrypt_text, encrypt_text
from app.database import session_scope
from app.models import Account
from app.telegram_client import make_client


def account():
    with session_scope() as db:
        row = Account(id=1, name="Test", phone="+12025550123", api_id=123,
                      api_hash_encrypted=encrypt_text("fictional-test-hash"),
                      status="login_required")
        db.add(row)
        db.flush()
        return row


def transport(client, outcomes):
    def send(*args, **kwargs):
        future = asyncio.get_running_loop().create_future()
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            future.set_exception(outcome)
        else:
            future.set_result(outcome)
        return future
    client._sender.send = Mock(side_effect=send)
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=False)
    async def switch_dc(dc):
        client.session.set_dc(dc, "127.0.0.1", 443)
    client._switch_dc = AsyncMock(side_effect=switch_dc)
    client.session.auth_key = AuthKey(bytes(range(256)))
    return client


def test_send_code_follows_dc_redirect_and_saves_session(monkeypatch):
    row = account()
    async def run():
        c = transport(make_client(row, for_login=True), iter([
            errors.PhoneMigrateError(None, capture=1),
            types.auth.SentCode(types.auth.SentCodeTypeApp(5), "test-code-hash"),
        ]))
        def factory(*args, **kwargs):
            assert kwargs.get("for_login") is True
            return c
        monkeypatch.setattr(workers, "make_client", factory)
        await workers.send_login_code(1)
        assert c._sender.send.call_count == 2
        c._switch_dc.assert_awaited_once_with(1)
        c.disconnect.assert_awaited_once()
        with session_scope() as db:
            saved = db.get(Account, 1)
            assert saved.phone_code_hash == "test-code-hash"
            session = workers.StringSession(decrypt_text(saved.login_temp_session_string_encrypted))
            assert session.dc_id == 1
            assert saved.session_string_encrypted is None
    asyncio.run(run())


def test_login_redirect_retries_are_bounded():
    row = account()
    async def run():
        c = transport(make_client(row, for_login=True), iter([
            errors.PhoneMigrateError(None, capture=1) for _ in range(3)
        ]))
        with pytest.raises(errors.PhoneMigrateError):
            await c.send_code_request(row.phone)
        assert c._sender.send.call_count == 3
    asyncio.run(run())


def test_login_flood_wait_is_not_retried(monkeypatch):
    row = account()
    async def run():
        c = transport(make_client(row, for_login=True), iter([
            errors.FloodWaitError(None, capture=120)
        ]))
        monkeypatch.setattr(workers, "make_client", lambda *a, **kw: c)
        with pytest.raises(errors.FloodWaitError):
            await workers.send_login_code(1)
        assert c._sender.send.call_count == 1
        c.disconnect.assert_awaited_once()
        with session_scope() as db:
            saved = db.get(Account, 1)
            assert saved.phone_code_hash is None
            assert saved.login_temp_session_string_encrypted is None
    asyncio.run(run())


def test_normal_client_still_does_not_retry():
    row = account()
    async def run():
        c = transport(make_client(row), iter([errors.PhoneMigrateError(None, capture=1)]))
        assert c._request_retries == 0
        with pytest.raises(ValueError):
            await c.send_code_request(row.phone)
        assert c._sender.send.call_count == 1
    asyncio.run(run())


def test_connect_failure_cleans_up_login_client(monkeypatch):
    row = account()
    async def run():
        c = transport(make_client(row, for_login=True), iter([]))
        c.connect.side_effect = OSError("simulated offline")
        monkeypatch.setattr(workers, "make_client", lambda *a, **kw: c)
        with pytest.raises(OSError):
            await workers.send_login_code(1)
        c.disconnect.assert_awaited_once()
        c._sender.send.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("exc, expected", [
    (ValueError("private diagnostic data"), "登录请求未完成"),
    (workers.TwoStepPasswordRequired(), "需要两步验证密码"),
    (errors.PhoneMigrateError(None, capture=1), "数据中心切换"),
    (errors.ApiIdPublishedFloodError(None), "公开 API 凭据"),
])
def test_login_errors_are_specific_and_redacted(exc, expected):
    message = login_error(exc)
    assert expected in message
    assert "private diagnostic data" not in message
