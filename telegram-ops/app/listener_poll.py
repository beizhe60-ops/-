"""Bounded reconciliation of new messages in explicitly configured chats.

The first check establishes a baseline, never replays existing history. Live
updates and polls share on_message and the persistent unique event key.
"""
import asyncio
import json
from datetime import datetime, timedelta
from telethon.errors import FloodWaitError
from app.database import session_scope
from app.models import Account
from app.relay_models import AccountProfile, RelayTask, SenderBinding

POLL_INTERVAL = 15
REQUEST_TIMEOUT = 15
PAGE_SIZE = 100
CHATS_PER_PASS = 10


def scopes(account_id):
    with session_scope() as db:
        account = db.get(Account, account_id)
        if not account or account.status not in ('active', 'flood_wait'):
            return {}
        if account.flood_wait_until and account.flood_wait_until > datetime.utcnow():
            return {}
        profile = db.get(AccountProfile, account_id)
        if not profile:
            return {}
        tasks = db.query(RelayTask).filter(RelayTask.enabled.is_(True), RelayTask.deleted_at.is_(None), RelayTask.relay_chat.is_not(None)).all()
        result = {}
        if profile.role == 'monitor':
            allowed = set(json.loads(profile.monitor_chat_ids))
            for task in tasks:
                if task.account_a == account_id:
                    for chat in set(json.loads(task.source_chats)) & allowed:
                        result.setdefault(chat, []).append((task.id, str(task.updated_at)))
        elif profile.role == 'sender':
            binding = db.get(SenderBinding, account_id)
            allowed = set(json.loads(binding.chat_ids)) if binding else set()
            for task in tasks:
                if task.relay_chat in allowed:
                    result.setdefault(task.relay_chat, []).append((task.id, str(task.updated_at)))
        return {chat: tuple(sorted(signature)) for chat, signature in result.items()}


async def poll_account(manager, account_id):
    async with manager.account_lock(account_id):
        worker = manager.workers.get(account_id)
        if not manager.running or not worker or not worker.client.is_connected():
            return
        allowed = scopes(account_id)
        states = manager.poll_cursors.setdefault(account_id, {})
        for chat in list(states):
            if chat not in allowed:
                del states[chat]
        # Oldest checked chat first: a large scope cannot starve its tail.
        chats = sorted(allowed, key=lambda chat: states.get(chat, {}).get('checked', 0))[:CHATS_PER_PASS]
        for chat in chats:
            if not manager.running or manager.workers.get(account_id) is not worker:
                return
            if scopes(account_id).get(chat) != allowed[chat]:
                states.pop(chat, None)
                continue
            state = states.setdefault(chat, {})
            state['checked'] = asyncio.get_running_loop().time()
            try:
                if state.get('signature') != allowed[chat]:
                    latest = await asyncio.wait_for(worker.client.get_messages(chat, limit=1), REQUEST_TIMEOUT)
                    # Changes made while the request was in flight invalidate this baseline.
                    if scopes(account_id).get(chat) != allowed[chat]:
                        states.pop(chat, None)
                        continue
                    state.update(signature=allowed[chat], cursor=latest[0].id if latest else 0)
                    manager.poll_status[account_id] = dict(at=datetime.utcnow().isoformat()+'Z', chat_id=chat, outcome='baseline')
                    continue
                rows = await asyncio.wait_for(worker.client.get_messages(chat, min_id=state['cursor'], reverse=True, limit=PAGE_SIZE), REQUEST_TIMEOUT)
                for message in sorted(rows, key=lambda row: row.id):
                    if message.id <= state['cursor']:
                        continue
                    if not manager.running or scopes(account_id).get(chat) != allowed[chat]:
                        states.pop(chat, None)
                        break
                    await asyncio.wait_for(manager.on_message(account_id, message), REQUEST_TIMEOUT)
                    # A live event may have already queued it; the database unique key wins.
                    state['cursor'] = message.id
                manager.poll_status[account_id] = dict(at=datetime.utcnow().isoformat()+'Z', chat_id=chat, outcome='checked', fetched=len(rows))
            except FloodWaitError as exc:
                with session_scope() as db:
                    account = db.get(Account, account_id)
                    if account and account.status in ('active', 'flood_wait'):
                        until = datetime.utcnow() + timedelta(seconds=exc.seconds)
                        account.flood_wait_until = max(account.flood_wait_until or until, until)
                        account.status = 'flood_wait'
                manager.poll_status[account_id] = dict(at=datetime.utcnow().isoformat()+'Z', chat_id=chat, outcome='platform_wait')
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep the cursor so a transient failure cannot silently skip messages.
                manager.poll_status[account_id] = dict(at=datetime.utcnow().isoformat()+'Z', chat_id=chat, outcome='error', error_type=type(exc).__name__)


async def poll_loop(manager):
    while manager.running:
        ids = sorted(manager.workers)
        for offset in range(0, len(ids), 4):
            batch = ids[offset:offset+4]
            results = await asyncio.gather(*(poll_account(manager, aid) for aid in batch), return_exceptions=True)
            for aid, result in zip(batch, results):
                if isinstance(result, Exception):
                    manager.poll_status[aid] = dict(at=datetime.utcnow().isoformat()+'Z', outcome='error', error_type=type(result).__name__)
        await asyncio.sleep(POLL_INTERVAL)
