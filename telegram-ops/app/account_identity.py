"""Telegram display metadata cached separately from account labels and routing.

ConsoleState stores this disposable cache; deleting it only removes display data.
No schema change or replacement of account/session/task identifiers is required.
"""
import asyncio
import contextlib
import json
from datetime import datetime, timedelta
from telethon.errors import FloodWaitError

from app.crypto import decrypt_text
from app.database import session_scope
from app.models import Account
from app.relay_models import ConsoleState
from app.telegram_client import make_client, CONNECT_TIMEOUT_SECONDS, DISCONNECT_TIMEOUT_SECONDS

PREFIX = "account_identity:"


class IdentitySyncError(ValueError):
    pass


def save_identity(db, account_id, user):
    user_id = getattr(user, "id", None)
    if type(user_id) is not int or user_id <= 0:
        raise IdentitySyncError("Telegram 未返回有效的账号资料，请稍后重试")
    data = {
        "telegram_user_id": user_id,
        "first_name": (getattr(user, "first_name", None) or "").strip(),
        "last_name": (getattr(user, "last_name", None) or "").strip(),
        "username": (getattr(user, "username", None) or "").strip() or None,
        "identity_synced_at": datetime.utcnow().isoformat() + "Z",
    }
    db.merge(ConsoleState(key=PREFIX + str(account_id), value=json.dumps(data, ensure_ascii=False)))
    return data


def identities_for(db, account_ids):
    if not account_ids:
        return {}
    rows = db.query(ConsoleState).filter(ConsoleState.key.in_(
        [PREFIX + str(aid) for aid in account_ids])).all()
    return {int(row.key[len(PREFIX):]): json.loads(row.value) for row in rows}


def display_fields(account, identity=None):
    identity = identity or {}
    name = " ".join(part for part in (identity.get("first_name"), identity.get("last_name")) if part)
    return {
        "name": name or account.name,
        "label": account.name,
        "username": identity.get("username"),
        "telegram_user_id": identity.get("telegram_user_id"),
        "identity_synced_at": identity.get("identity_synced_at"),
    }


async def refresh_identity(account_id, client=None):
    """Caller holds the account lock. Reuse a live client without disrupting it."""
    with session_scope() as db:
        account = db.get(Account, account_id)
        if not account or not account.session_string_encrypted:
            raise IdentitySyncError("请先完成账号登录")
        if account.flood_wait_until and account.flood_wait_until > datetime.utcnow():
            raise IdentitySyncError("Telegram 要求等待，请稍后同步资料")
        session = account.session_string_encrypted
    owned = client is None
    if owned:
        client = make_client(account, decrypt_text(session))
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
            if owned:
                await client.connect()
                if not await client.is_user_authorized():
                    raise IdentitySyncError("账号登录已失效，请重新登录")
            user = await client.get_me()
        with session_scope() as db:
            current = db.get(Account, account_id)
            if not current or current.session_string_encrypted != session:
                raise IdentitySyncError("账号登录状态已变化，请重新同步")
            return save_identity(db, account_id, user)
    except FloodWaitError as exc:
        with session_scope() as db:
            current = db.get(Account, account_id)
            if current and current.session_string_encrypted == session:
                current.flood_wait_until = max(current.flood_wait_until or datetime.min,
                                              datetime.utcnow() + timedelta(seconds=exc.seconds))
                current.status = "flood_wait"
        raise
    finally:
        if owned:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), DISCONNECT_TIMEOUT_SECONDS)
