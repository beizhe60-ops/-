import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from telethon.tl.types import User
from telethon.errors import FloodWaitError
from app.database import session_scope
from app.models import Account
from app.relay_models import RelayJob, RelayTask
from app.listener_poll import poll_account
from test_relay import seed, engine, event


def message(mid, text='咨询', uid=999):
    return event(-1001, User(id=uid, username='target_user'), text, mid)


def test_poll_new_messages_without_push_and_deduplicate_live_event():
    seed();e=engine();c=e.workers[1].client
    c.get_messages=AsyncMock(side_effect=[[message(10)], [message(11)], [message(12)]])
    async def run():
        await poll_account(e,1)
        with session_scope() as db: assert db.query(RelayJob).count()==0
        await poll_account(e,1)
        await e.on_message(1,message(12))
        await poll_account(e,1)
    asyncio.run(run())
    with session_scope() as db: assert [(r.message_id,r.stage) for r in db.query(RelayJob).order_by(RelayJob.id)]==[(11,'relay'),(12,'relay')]
    assert c.get_messages.call_args.kwargs==dict(min_id=11,reverse=True,limit=100)
    c.send_message.assert_not_awaited()


def test_failed_fetch_does_not_advance_cursor():
    seed();e=engine();c=e.workers[1].client
    c.get_messages=AsyncMock(side_effect=[[message(10)],TimeoutError(),[message(11)]])
    async def run():
        await poll_account(e,1);await poll_account(e,1)
        assert e.poll_cursors[1][-1001]['cursor']==10
        assert e.poll_status[1]['error_type']=='TimeoutError'
        await poll_account(e,1)
    asyncio.run(run())
    with session_scope() as db:assert db.query(RelayJob).one().message_id==11


def test_poll_respects_pause_during_fetch():
    t=seed();e=engine();c=e.workers[1].client
    async def fetch(*a,**kw):
        if kw.get('limit')==1:return [message(10)]
        with session_scope() as db:db.get(RelayTask,t.id).enabled=False
        return [message(11)]
    c.get_messages=AsyncMock(side_effect=fetch)
    async def run():await poll_account(e,1);await poll_account(e,1)
    asyncio.run(run())
    with session_scope() as db:assert db.query(RelayJob).count()==0
    assert not e.poll_cursors[1]


def test_poll_filters_and_account_wait():
    seed();e=engine();c=e.workers[1].client
    c.get_messages=AsyncMock(side_effect=[[message(10)],[message(11,'无关键词'),message(12,uid=111)],FloodWaitError(request=None,capture=45)])
    async def run():
        await poll_account(e,1);await poll_account(e,1);await poll_account(e,1);await poll_account(e,1)
    asyncio.run(run())
    assert c.get_messages.await_count==3
    with session_scope() as db:
        assert db.query(RelayJob).count()==0
        assert db.get(Account,1).flood_wait_until>datetime.utcnow()
        assert db.get(Account,2).status=='active'


def test_change_scope_rebaselines_without_replaying_history():
    t=seed();e=engine();c=e.workers[1].client
    c.get_messages=AsyncMock(side_effect=[[message(10)],[message(50)]])
    async def run():
        await poll_account(e,1)
        with session_scope() as db:db.get(RelayTask,t.id).keywords='价格'
        await poll_account(e,1)
    asyncio.run(run())
    assert c.get_messages.call_args.kwargs=={'limit':1}
    with session_scope() as db:assert db.query(RelayJob).count()==0
