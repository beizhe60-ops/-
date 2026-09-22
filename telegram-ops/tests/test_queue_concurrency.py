"""Bounded account scheduling and send/configuration race regressions."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import User

from app import relay_engine, console_api
from app.database import session_scope
from app.models import Account
from app.relay_models import AccountProfile, RelayJob, RelayTask, ContactReceipt, SenderBinding
from test_relay import seed, engine


def setup_monitors(count=2):
    seed()
    e = engine()
    with session_scope() as db:
        for aid in range(1, count + 1):
            if aid > 2:
                db.add(Account(id=aid, name=f"Monitor {aid}", phone=f"+12025550{aid:03}",
                               api_id=1, api_hash_encrypted="fictional", status="active", send_enabled=True))
                db.add(AccountProfile(account_id=aid, role="monitor", monitor_chat_ids="[-1001]"))
                e.workers[aid] = SimpleNamespace(client=SimpleNamespace(is_connected=lambda: True,
                    send_message=AsyncMock(return_value=SimpleNamespace(id=700)), disconnect=AsyncMock()))
            else:
                db.get(AccountProfile, aid).role = "monitor"
                db.get(AccountProfile, aid).monitor_chat_ids = "[-1001]"
            if aid > 1:
                db.add(RelayTask(id=aid, name=f"Task {aid}", account_a=aid, account_b=aid,
                                source_chats="[-1001]", relay_chat=-1002, keywords="咨询", template="", enabled=True))
    for worker in e.workers.values():
        worker.client.disconnect = AsyncMock()
    return e


def add_job(aid, mid):
    with session_scope() as db:
        job = RelayJob(task_id=aid, stage="relay", account_id=aid, chat_id=-1001,
                       message_id=mid, username="target_user", original_text="咨询", text=f"job-{mid}")
        db.add(job)
        db.flush()
        return job.id


def statuses():
    with session_scope() as db:
        return {j.id: j.status for j in db.query(RelayJob)}


async def drain(e):
    if e.send_tasks:
        await asyncio.gather(*list(e.send_tasks.values()))
    await asyncio.sleep(0)


def test_offline_head_does_not_starve_later_account():
    e = setup_monitors()
    for i in range(10):
        add_job(1, i)
    good = add_job(2, 100)
    del e.workers[1]
    asyncio.run(e.process_pending_queue())
    assert statuses()[good] == "sent"
    assert list(statuses().values()).count("pending") == 10


def test_slow_account_does_not_block_other_ticks_or_configuration(monkeypatch):
    e = setup_monitors()
    first, cancelled, second = add_job(1, 1), add_job(1, 2), add_job(2, 3)
    monkeypatch.setattr(console_api, "manager", e)

    async def run():
        entered, release, sent_b = asyncio.Event(), asyncio.Event(), asyncio.Event()
        destinations = []

        async def slow(destination, text, **kwargs):
            destinations.append(destination)
            entered.set()
            await release.wait()
            return SimpleNamespace(id=700)

        async def fast(*args, **kwargs):
            sent_b.set()
            return SimpleNamespace(id=701)

        e.workers[1].client.send_message.side_effect = slow
        e.workers[2].client.send_message.side_effect = fast
        await e.process_pending_queue(wait=False)
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(sent_b.wait(), 1)
        await asyncio.sleep(0.02)
        sent_b.clear()
        third = add_job(2, 4)
        await e.process_pending_queue(wait=False)
        await asyncio.wait_for(sent_b.wait(), 1)
        with session_scope() as db:
            task = db.get(RelayTask, 1)
            payload = console_api.task_json(task)
            payload.pop("id"); payload.pop("enabled")
            payload["relay_chat"] = -1003
            payload["account_b"] = None
            await asyncio.wait_for(console_api.edit_task(1, console_api.TaskInput(**payload), db), 1)
        assert statuses()[cancelled] == "cancelled"
        release.set()
        await drain(e)
        assert destinations == [-1002]  # Already in flight: never reroute to the edited target.
        assert all(statuses()[j] == "sent" for j in (first, second, third))
        await e.stop()

    asyncio.run(run())


def test_parallelism_is_bounded_and_accounts_are_rotated():
    e = setup_monitors(6)
    for aid in range(1, 7):
        add_job(aid, aid)

    async def run():
        gate = asyncio.Event()
        started = []
        async def slow(*args, **kwargs):
            started.append(args[1])
            await gate.wait()
            return SimpleNamespace(id=700)
        for w in e.workers.values():
            w.client.send_message.side_effect = slow
        await e.process_pending_queue(wait=False)
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(e.send_tasks) == relay_engine.MAX_ACCOUNT_OPERATIONS == 4
        assert len(started) == 4
        await e.process_pending_queue(wait=False)
        assert len(e.send_tasks) == 4
        gate.set(); await drain(e)
        # Even with fresh work on account 1, accounts 5/6 receive their turn.
        add_job(1, 99)
        await e.process_pending_queue()
        assert all(s == "sent" for s in statuses().values())
        await e.stop()
    asyncio.run(run())


def test_same_account_order_and_duplicate_dispatch():
    e = setup_monitors()
    add_job(1, 1); add_job(1, 2)

    async def run():
        seen = []
        async def send(dest, text, **kw):
            seen.append(text)
            await asyncio.sleep(0.01)
            return SimpleNamespace(id=700)
        e.workers[1].client.send_message.side_effect = send
        await asyncio.gather(e.process_pending_queue(), e.process_pending_queue())
        assert seen == ["job-1", "job-2"]
        await e.stop()
    asyncio.run(run())


def test_flood_wait_keeps_account_and_does_not_block_other_account():
    e = setup_monitors()
    first, later, good = add_job(1, 1), add_job(1, 2), add_job(2, 3)
    e.workers[1].client.send_message.side_effect = FloodWaitError(None, capture=3600)
    async def run():
        await e.process_pending_queue()
        await e.process_pending_queue()
        assert statuses() == {first: "waiting", later: "waiting", good: "sent"}
        assert e.workers[1].client.send_message.call_count == 1
        newly_waiting = add_job(1, 90)
        await e.process_pending_queue()
        assert statuses()[newly_waiting] == "waiting"
        assert e.workers[1].client.send_message.call_count == 1
        with session_scope() as db:
            assert db.get(RelayJob, first).account_id == 1
            assert db.get(RelayJob, first).due_at > datetime.utcnow()
        await e.stop()
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["unbind", "permission", "pause", "template"])
def test_dm_rechecks_configuration_after_username_lookup(monkeypatch, mutation):
    t = seed(); e = engine()
    monkeypatch.setattr(console_api, "manager", e)
    e.enqueue(t, "dm", 2, -1002, 1, 999, "target_user", "群", "咨询", "文案")

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def lookup(*args):
            entered.set(); await release.wait()
            return User(id=999, username="target_user")
        e.workers[2].client.get_entity.side_effect = lookup
        task = asyncio.create_task(e.process_pending_queue())
        await asyncio.wait_for(entered.wait(), 1)
        with session_scope() as db:
            if mutation in ("unbind", "template"):
                await console_api.receive_groups(2, console_api.ReceiveGroupsInput(
                    chat_ids=[] if mutation == "unbind" else [-1002], template="新文案"), db)
            elif mutation == "permission":
                db.get(Account, 2).private_message_enabled = False
            else:
                db.get(RelayTask, t.id).enabled = False
        release.set(); await task
        assert e.workers[2].client.send_message.call_count == 0
        with session_scope() as db:
            assert db.query(ContactReceipt).count() == 0
        await e.stop()
    asyncio.run(run())


def test_stop_cancels_active_send_as_unknown_without_retry():
    e = setup_monitors()
    first, later = add_job(1, 1), add_job(1, 2)
    async def run():
        entered = asyncio.Event()
        async def hang(*args, **kwargs):
            entered.set(); await asyncio.Future()
        e.workers[1].client.send_message.side_effect = hang
        await e.process_pending_queue(wait=False)
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(e.stop(), 1)
        assert statuses() == {first: "unknown", later: "pending"}
        assert not e.send_tasks and not e.connection_tasks and not e.workers
    asyncio.run(run())


def test_network_send_timeout_is_unknown(monkeypatch):
    monkeypatch.setattr(relay_engine, "SEND_TIMEOUT_SECONDS", 0.01)
    e = setup_monitors(); job = add_job(1, 1)
    async def hang(*args, **kwargs):
        await asyncio.Future()
    e.workers[1].client.send_message.side_effect = hang
    async def run():
        await e.process_pending_queue()
        await e.process_pending_queue()
        assert statuses()[job] == "unknown"
        assert e.workers[1].client.send_message.call_count == 1
        await e.stop()
    asyncio.run(run())


def test_two_accounts_same_user_only_one_receipt_and_send():
    t = seed(); e = engine()
    with session_scope() as db:
        db.get(AccountProfile, 1).role = "sender"
        db.add(SenderBinding(account_id=1, chat_ids="[-1002]", template="文案"))
        for aid in (1, 2):
            db.add(RelayJob(task_id=t.id, stage="dm", account_id=aid, chat_id=-1002,
                message_id=aid, username="target_user", original_text="咨询", text="文案"))
    async def run():
        await e.process_pending_queue()
        assert sorted(statuses().values()) == ["sent", "skipped"]
        assert sum(w.client.send_message.call_count for w in e.workers.values()) == 1
        with session_scope() as db:
            assert db.query(ContactReceipt).count() == 1
        await e.stop()
    asyncio.run(run())


def test_sync_uses_account_lock_without_blocking_other_accounts(monkeypatch):
    e = setup_monitors()
    monkeypatch.setattr(console_api, "manager", e)
    with session_scope() as db:
        db.get(Account, 1).session_string_encrypted = "fictional"
    good = add_job(2, 1)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def sync_stub(account_id):
            entered.set(); await release.wait(); return 2
        monkeypatch.setattr(console_api, "sync_account_chats", sync_stub)
        with session_scope() as db:
            sync = asyncio.create_task(console_api.sync(1, db))
            await asyncio.wait_for(entered.wait(), 1)
            assert e.account_lock(1).locked()
            async with asyncio.timeout(1):
                async with e.lock:
                    pass
                await e.process_pending_queue()
            assert statuses()[good] == "sent"
            release.set(); await sync
        await e.stop()
    asyncio.run(run())


def test_stopped_engine_ignores_delayed_enqueue():
    t = seed(); e = engine(); e.running = False
    e.enqueue(t, "relay", 1, -1001, 99, 999, "target_user", "群", "咨询", "文案")
    assert statuses() == {}


@pytest.mark.parametrize("failure", ["connect", "timeout"])
def test_group_sync_cleans_client_on_initialization_failure(monkeypatch, failure):
    from app import workers
    setup_monitors()
    with session_scope() as db:
        db.get(Account, 1).session_string_encrypted = "fictional"
    client = SimpleNamespace(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True),
                             get_dialogs=AsyncMock(), disconnect=AsyncMock())
    if failure == "connect":
        client.connect.side_effect = RuntimeError("simulated failure")
    else:
        async def hang():
            await asyncio.Future()
        client.get_dialogs.side_effect = hang
    monkeypatch.setattr(workers, "make_client", lambda *a, **kw: client)
    monkeypatch.setattr(workers, "decrypt_text", lambda value: value)
    monkeypatch.setattr(workers, "CONNECT_TIMEOUT_SECONDS", 0.01)
    async def run():
        with pytest.raises(RuntimeError if failure == "connect" else TimeoutError):
            await workers.sync_account_chats(1)
        client.disconnect.assert_awaited_once()
    asyncio.run(run())


def test_failed_send_yields_batch_without_reassigning_remaining_job():
    e = setup_monitors()
    failed, pending = add_job(1, 1), add_job(1, 2)
    e.workers[1].client.send_message.side_effect = OSError("simulated network failure")
    async def run():
        await e.process_pending_queue()
        assert statuses() == {failed: "unknown", pending: "pending"}
        with session_scope() as db:
            assert db.get(RelayJob, pending).account_id == 1
        await e.stop()
    asyncio.run(run())
