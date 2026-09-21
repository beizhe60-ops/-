import asyncio
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock
import pytest
from telethon.tl.types import User
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError
from app.database import session_scope
from app.models import Account, UserGuard
from app.relay_models import RelayTask, RelayJob, AccountProfile, SenderBinding
from app.relay_logic import filter_message, format_relay, parse_relay
from app.relay_engine import RelayEngine


def seed():
    with session_scope() as db:
        db.add_all(
            [
                Account(
                    id=i,
                    name=f"Account {i}",
                    phone=f"+1234567890{i}",
                    api_id=1,
                    api_hash_encrypted="test",
                    status="active",
                    send_enabled=True,
                    private_message_enabled=True,
                )
                for i in (1, 2)
            ]
        )
        t = RelayTask(
            name="需求监控",
            account_a=1,
            account_b=2,
            source_chats="[-1001]",
            relay_chat=-1002,
            keywords="咨询",
            exclude_keywords="广告",
            ignore_users="",
            match_mode="any",
            template="您好 {{ username }}",
            enabled=True,
        )
        db.add(t)
        db.add_all(
            [
                AccountProfile(
                    account_id=1, role="monitor", monitor_chat_ids="[-1001]"
                ),
                AccountProfile(account_id=2, role="sender", monitor_chat_ids="[]"),
            ]
        )
        db.add(
            SenderBinding(
                account_id=2, chat_ids="[-1002]", template="您好 {{ username }}"
            )
        )
        db.flush()
        return t


def engine():
    e = RelayEngine()
    e.running = True
    e.identities = {1: 111, 2: 222}
    for i in (1, 2):
        c = SimpleNamespace(
            is_connected=lambda: True,
            send_message=AsyncMock(return_value=SimpleNamespace(id=700)),
            get_entity=AsyncMock(return_value=User(id=999, username="target_user")),
        )
        e.workers[i] = SimpleNamespace(client=c)
    return e


def event(chat_id, sender, text, mid=10):
    return SimpleNamespace(
        is_group=True,
        is_channel=False,
        chat_id=chat_id,
        sender_id=sender.id,
        out=False,
        raw_text=text,
        id=mid,
        get_sender=AsyncMock(return_value=sender),
        get_chat=AsyncMock(return_value=SimpleNamespace(title="产品-讨论群")),
    )


def dm(e, t, mid=50):
    e.enqueue(t, "dm", 2, -1002, mid, 999, "target_user", "群", "咨询", "你好")


def test_filters():
    t = seed()
    assert filter_message(t, "咨询价格", "target_user")[0]
    assert not filter_message(t, "广告咨询", "target_user")[0]
    assert not filter_message(t, "咨询价格", "")[0]
    t.keywords = "咨询,价格"
    t.match_mode = "all"
    assert not filter_message(t, "咨询", "target_user")[0]
    assert filter_message(t, "咨询价格", "target_user")[0]
    t.match_mode = "exact"
    assert filter_message(t, "咨询", "target_user")[0]
    assert not filter_message(t, "咨询价格", "target_user")[0]
    t.ignore_users = "@target_user"
    assert not filter_message(t, "咨询", "target_user")[0]


def test_tail_username_and_size():
    text = format_relay("群组", "@other_user 私信我\n咨询", "target_user")
    assert text == "群组-@other_user 私信我 咨询-@target_user"
    assert parse_relay(text).username == "target_user"
    for bad in ["随便 @target_user", text + " 后缀", text + "\n"]:
        assert parse_relay(bad) is None
    with pytest.raises(ValueError):
        format_relay("群组", "😀" * 2200, "target_user")


def test_a_to_relay_to_b_and_repeated_events():
    seed()
    e = engine()

    async def run():
        ev = event(-1001, User(id=999, username="target_user"), "咨询 @other_user 服务")
        await e.on_message(1, ev)
        await e.on_message(1, ev)
        await e.process_pending_queue()
        a = e.workers[1].client.send_message
        assert a.await_count == 1
        relay = a.await_args.args[1]
        assert relay == "产品-讨论群-咨询 @other_user 服务-@target_user"
        ev = event(-1002, User(id=111, username="account_a"), relay, 700)
        await e.on_message(2, ev)
        await e.on_message(2, ev)
        await e.process_pending_queue()
        b = e.workers[2].client.send_message
        assert b.await_count == 1
        assert b.await_args.args[0].id == 999
        assert b.await_args.args[1] == "您好 @target_user"
        with session_scope() as db:
            jobs = db.query(RelayJob).order_by(RelayJob.id).all()
            assert len(jobs) == 2
            assert all(j.status == "sent" for j in jobs)
            assert jobs[1].source_title == "产品-讨论群"

    asyncio.run(run())


def test_ignore_untrusted_and_outgoing():
    seed()
    e = engine()

    async def run():
        await e.on_message(
            2, event(-1002, User(id=444, username="intruder"), "群-咨询-@target_user")
        )
        ev = event(-1001, User(id=111, username="account_a"), "咨询")
        ev.out = True
        await e.on_message(1, ev)
        with session_scope() as db:
            assert db.query(RelayJob).count() == 0

    asyncio.run(run())


def test_global_dedup():
    t = seed()
    e = engine()
    dm(e, t)
    dm(e, t, 51)
    asyncio.run(e.process_pending_queue())
    assert e.workers[2].client.send_message.await_count == 1
    with session_scope() as db:
        assert [j.status for j in db.query(RelayJob).order_by(RelayJob.id)] == [
            "sent",
            "skipped",
        ]


def test_blocklist():
    t = seed()
    e = engine()
    dm(e, t)
    with session_scope() as db:
        db.add(UserGuard(telegram_user_id=999, blacklisted=True))
    asyncio.run(e.process_pending_queue())
    e.workers[2].client.send_message.assert_not_awaited()


def test_username_reassignment():
    t = seed()
    e = engine()
    dm(e, t)
    e.workers[2].client.get_entity.return_value = User(id=888, username="target_user")
    asyncio.run(e.process_pending_queue())
    e.workers[2].client.send_message.assert_not_awaited()
    with session_scope() as db:
        assert db.query(RelayJob).one().status == "skipped"


def test_pause_and_permissions():
    t = seed()
    e = engine()
    dm(e, t)
    with session_scope() as db:
        db.get(RelayTask, t.id).enabled = False
    asyncio.run(e.process_pending_queue())
    e.workers[2].client.send_message.assert_not_awaited()
    with session_scope() as db:
        db.get(RelayTask, t.id).enabled = True
        db.get(Account, 2).private_message_enabled = False
    asyncio.run(e.process_pending_queue())
    e.workers[2].client.send_message.assert_not_awaited()


def test_flood_wait():
    t = seed()
    e = engine()
    dm(e, t)
    e.workers[2].client.send_message.side_effect = FloodWaitError(None, capture=3600)
    asyncio.run(e.process_pending_queue())
    asyncio.run(e.process_pending_queue())
    assert e.workers[2].client.send_message.await_count == 1
    with session_scope() as db:
        j = db.query(RelayJob).one()
        assert j.status == "waiting"
        assert j.due_at > datetime.utcnow()


@pytest.mark.parametrize(
    "exc,status",
    [(TimeoutError("lost"), "unknown"), (UserPrivacyRestrictedError(None), "failed")],
)
def test_failure_not_retried(exc, status):
    t = seed()
    e = engine()
    dm(e, t)
    e.workers[2].client.send_message.side_effect = exc
    asyncio.run(e.process_pending_queue())
    asyncio.run(e.process_pending_queue())
    assert e.workers[2].client.send_message.await_count == 1
    with session_scope() as db:
        assert db.query(RelayJob).one().status == status


def test_restart_unknown():
    t = seed()
    e = engine()
    dm(e, t)
    with session_scope() as db:
        db.query(RelayJob).update({"status": "sending"})
    e.running = False
    e.workers = {}

    async def run():
        await e.start()
        await e.stop()

    asyncio.run(run())
    with session_scope() as db:
        assert db.query(RelayJob).one().status == "unknown"


def test_two_step_login_resumes_without_reusing_code(monkeypatch):
    from app import workers
    from app.crypto import encrypt_text
    from app.relay_models import ConsoleState
    from telethon.errors import SessionPasswordNeededError

    seed()
    with session_scope() as db:
        a = db.get(Account, 1)
        a.phone_code_hash = "code-hash"
        a.login_temp_session_string_encrypted = encrypt_text("temp-session")
    client = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        session=object(),
        sign_in=AsyncMock(side_effect=SessionPasswordNeededError(None)),
    )
    monkeypatch.setattr(workers, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(workers.StringSession, "save", lambda session: "saved-session")
    with pytest.raises(ValueError):
        asyncio.run(workers.verify_login_code(1, "12345"))
    with session_scope() as db:
        assert db.get(ConsoleState, "login_2fa:1") is not None
    client.sign_in = AsyncMock()
    asyncio.run(workers.verify_login_code(1, "12345", "correct-password"))
    client.sign_in.assert_awaited_once_with(password="correct-password")
    with session_scope() as db:
        assert db.get(ConsoleState, "login_2fa:1") is None
        assert db.get(Account, 1).status == "active"


def test_monitor_account_scope_is_enforced_before_fetching_sender():
    seed()
    e = engine()
    # Even a stale task that names this group cannot override the account scope.
    with session_scope() as db:
        db.get(AccountProfile, 1).monitor_chat_ids = "[]"
    ev = event(-1001, User(id=999, username="target_user"), "咨询")
    asyncio.run(e.on_message(1, ev))
    ev.get_sender.assert_not_awaited()
    with session_scope() as db:
        assert db.query(RelayJob).count() == 0


def test_weighted_rotation_persists_and_duplicate_does_not_advance():
    from app.models import Chat
    from app.relay_models import SenderWeight, ConsoleState
    from collections import Counter

    t = seed()
    e = engine()
    with session_scope() as db:
        db.add(
            Account(
                id=3,
                name="B3",
                phone="+12345678903",
                api_id=1,
                api_hash_encrypted="test",
                status="active",
                send_enabled=True,
                private_message_enabled=True,
            )
        )
        db.flush()
        db.add(AccountProfile(account_id=3, role="sender"))
        db.add(
            Chat(account_id=3, telegram_chat_id=-1002, title="relay", type="supergroup")
        )
        db.add(SenderWeight(account_id=2, weight=3))
        db.add(SenderBinding(account_id=3, chat_ids="[-1002]", template="您好"))
    e.workers[3] = e.workers[2]
    for mid in range(8):
        dm(e, t, mid)
        if mid == 3:
            e2 = engine()
            e2.workers[3] = e2.workers[2]
            e = e2
    with session_scope() as db:
        counts = Counter(j.account_id for j in db.query(RelayJob).all())
        assert counts == {2: 6, 3: 2}
        before = db.get(ConsoleState, "sender_rotation:-1002").value
    dm(e, t, 7)
    with session_scope() as db:
        assert db.get(ConsoleState, "sender_rotation:-1002").value == before
        db.get(Account, 2).send_enabled = False
    dm(e, t, 8)
    with session_scope() as db:
        assert db.query(RelayJob).filter_by(message_id=8).one().account_id == 3
        db.get(Account, 3).flood_wait_until = datetime(2099, 1, 1)
    dm(e, t, 9)
    with session_scope() as db:
        # No eligible sender: retain original receiver and let it wait.
        assert db.query(RelayJob).filter_by(message_id=9).one().account_id == 2


def test_monitor_only_forwards_exact_destination_without_sender():
    t = seed()
    with session_scope() as db:
        t = db.get(RelayTask, t.id)
        t.account_b = t.account_a
        t.template = ""
        db.get(SenderBinding, 2).chat_ids = "[]"
    e = engine()
    del e.workers[2]

    async def run():
        await e.on_message(
            1, event(-1009, User(id=999, username="target_user"), "咨询")
        )
        await e.on_message(
            1, event(-1001, User(id=999, username="target_user"), "咨询")
        )
        await e.process_pending_queue()

    asyncio.run(run())
    assert e.workers[1].client.send_message.await_count == 1
    assert e.workers[1].client.send_message.await_args.args[0] == -1002


def test_explicit_receivers_replace_fixed_b_and_deduplicate_events():
    from app.models import Chat

    t = seed()
    with session_scope() as db:
        db.get(RelayTask, t.id).template = ""
        for aid in [3, 4]:
            db.add(
                Account(
                    id=aid,
                    name=f"B{aid}",
                    phone=f"+1234567890{aid}",
                    api_id=1,
                    api_hash_encrypted="test",
                    status="active",
                    send_enabled=True,
                    private_message_enabled=True,
                )
            )
            db.flush()
            db.add(AccountProfile(account_id=aid, role="sender"))
            db.add(
                Chat(
                    account_id=aid,
                    telegram_chat_id=-1002,
                    title="same name",
                    type="supergroup",
                )
            )
        db.add(
            SenderBinding(
                account_id=3, chat_ids="[-1002]", template="绑定文案 {{ username }}"
            )
        )
        db.get(SenderBinding, 2).chat_ids = "[-1009]"
    e = engine()
    e.workers[3] = e.workers[2]
    e.workers[4] = e.workers[2]

    async def run():
        incoming = event(
            -1002, User(id=111, username="account_a"), "群-咨询-@target_user"
        )
        await e.on_message(2, incoming)  # old fixed B is no longer bound
        await e.on_message(4, incoming)  # synced membership does not grant a binding
        with session_scope() as db:
            assert db.query(RelayJob).count() == 0
        await e.on_message(3, incoming)
        await e.on_message(3, incoming)
        await e.on_message(
            3, event(-1009, User(id=111, username="account_a"), incoming.raw_text)
        )
        with session_scope() as db:
            jobs = db.query(RelayJob).all()
            assert len(jobs) == 1 and jobs[0].account_id == 3
            assert jobs[0].text == "绑定文案 @target_user"
        await e.process_pending_queue()

    asyncio.run(run())
    assert e.workers[3].client.send_message.await_count == 1


def test_binding_removed_before_send_blocks_queued_message():
    t = seed()
    e = engine()
    dm(e, t)
    with session_scope() as db:
        db.get(SenderBinding, 2).chat_ids = "[-1009]"
    asyncio.run(e.process_pending_queue())
    e.workers[2].client.send_message.assert_not_awaited()
    with session_scope() as db:
        assert db.query(RelayJob).one().status == "cancelled"


def test_route_changed_during_sender_lookup_drops_stale_event():
    t = seed()
    e = engine()
    ev = event(-1001, User(id=999, username="target_user"), "咨询")

    async def change_route():
        with session_scope() as db:
            db.get(RelayTask, t.id).relay_chat = -1009
        return User(id=999, username="target_user")

    ev.get_sender = change_route
    asyncio.run(e.on_message(1, ev))
    with session_scope() as db:
        assert db.query(RelayJob).count() == 0
