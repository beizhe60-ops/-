import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from telethon.errors import FloodWaitError
from app.main import app
from app.database import session_scope
from app.models import Account
from app.relay_models import BroadcastTask, BroadcastDelivery, AiSummaryMessage, AiSummaryRun, ConsoleState, RelayTask
from app.broadcast import deliver_next, recover_broadcasts
from app.relay_engine import RelayEngine
from app.crypto import encrypt_text
from test_console_api import login, seed, HEAD


def draft(c, **extra):
    data = dict(request_key='test-request-00000001', name='公告', account_id=1, targets=[-1001,-1002,-1001], text='测试通知')
    data.update(extra)
    return c.post('/api/broadcasts', json=data, headers=HEAD)


def test_group_campaign_lifecycle_and_validation():
    seed()
    with TestClient(app) as c:
        assert c.get('/api/broadcasts').status_code == 401
        login(c)
        assert c.get('/console/broadcasts').status_code == 200
        assert draft(c, targets=[True]).status_code == 422
        assert draft(c, targets=[123]).status_code == 422
        assert draft(c, targets=['-1001']).status_code == 422
        assert draft(c, scheduled_at='2020-01-01T10:00:00+08:00').status_code == 422
        assert draft(c, scheduled_at='2099-01-01T10:00:00').status_code == 422
        r=draft(c).json()
        assert r['status']=='draft' and len(r['targets'])==2
        assert draft(c).json()['id']==r['id']
        assert draft(c, text='另一条通知').status_code == 409
        url=f"/api/broadcasts/{r['id']}"
        for _ in range(2):
            assert c.post(url+'/start',headers=HEAD).status_code==200
        assert len(c.get(url+'/records').json())==2
        c.post(url+'/pause',headers=HEAD)
        assert c.get('/api/broadcasts').json()[0]['status']=='paused'
        c.post(url+'/start',headers=HEAD)
        c.post(url+'/cancel',headers=HEAD)
        assert all(j['status']=='cancelled' for j in c.get(url+'/records').json())
        assert c.post(url+'/start',headers=HEAD).status_code==409
        with session_scope() as db:
            assert db.query(BroadcastTask).count()==1
            assert db.query(RelayTask).count()==0


def queued():
    seed()
    with session_scope() as db:
        t=BroadcastTask(request_key='seed-000000000001',name='通知',account_id=1,targets='[-1001]',text='hello',status='queued')
        db.add(t); db.flush()
        db.add(BroadcastDelivery(task_id=t.id,chat_id=-1001))
    engine=RelayEngine();engine.running=True
    send=AsyncMock(return_value=SimpleNamespace(id=22))
    engine.workers[1]=SimpleNamespace(client=SimpleNamespace(is_connected=lambda:True,send_message=send))
    return engine,send


def test_send_pause_permission_and_idempotence():
    engine,send=queued()
    with session_scope() as db: db.get(BroadcastTask,1).status='paused'
    asyncio.run(deliver_next(engine,1));assert send.await_count==0
    with session_scope() as db:
        db.get(BroadcastTask,1).status='queued'; db.get(Account,1).send_enabled=False
    asyncio.run(deliver_next(engine,1));assert send.await_count==0
    with session_scope() as db: db.get(Account,1).send_enabled=True
    asyncio.run(deliver_next(engine,1));asyncio.run(deliver_next(engine,1))
    assert send.await_count==1
    with session_scope() as db:
        assert db.get(BroadcastDelivery,1).status=='sent'
        assert db.get(BroadcastDelivery,1).sent_message_id==22


def test_schedule_wait_unknown_and_restart():
    engine,send=queued()
    with session_scope() as db: db.get(BroadcastDelivery,1).due_at=datetime.utcnow()+timedelta(hours=1)
    asyncio.run(deliver_next(engine,1));assert not send.called
    with session_scope() as db: db.get(BroadcastDelivery,1).due_at=datetime.utcnow()
    send.side_effect=FloodWaitError(request=None,capture=30)
    asyncio.run(deliver_next(engine,1))
    with session_scope() as db:
        assert db.get(BroadcastDelivery,1).status=='waiting'
        assert db.get(Account,1).flood_wait_until>datetime.utcnow()
        db.get(Account,1).flood_wait_until=None
        db.get(BroadcastDelivery,1).due_at=datetime.utcnow()
    send.side_effect=TimeoutError()
    asyncio.run(deliver_next(engine,1));asyncio.run(deliver_next(engine,1))
    assert send.await_count==2
    with session_scope() as db:
        assert db.get(BroadcastDelivery,1).status=='unknown'
        db.get(BroadcastDelivery,1).status='sending'
    recover_broadcasts();recover_broadcasts()
    with session_scope() as db: assert db.get(BroadcastDelivery,1).status=='unknown'


def ai_seed(trigger='manual'):
    seed()
    config=dict(enabled=True, account_id=1,source_chat_ids=[-1001],api_key_encrypted=encrypt_text('test-key'),api_url='https://ai.example/v1/chat/completions',model='test',trigger=trigger,target_type='group',target_id=-1002,message_count=1,trigger_count=2,lookback_hours=24,schedule_time='23:00')
    with session_scope() as db:
        db.add(ConsoleState(key='ai_summary_settings',value=json.dumps(config)))
        for aid,cid,mid in [(1,-1001,1),(1,-1001,2),(2,-1001,1),(1,-999,3)]:
            db.add(AiSummaryMessage(account_id=aid,chat_id=cid,message_id=mid,chat_title='测试群',sender_name='用户',text='sample'))
    return config


def test_ai_manual_and_preview_scope(monkeypatch):
    ai_seed()
    seen=[]
    monkeypatch.setattr('app.relay_engine.request_summary',lambda u,k,m,p:seen.extend(p) or '摘要预览')
    engine=RelayEngine()
    assert asyncio.run(engine.generate_ai_summary())['reason']=='仅手动生成'
    assert seen==[]
    r=asyncio.run(engine.generate_ai_summary(force=True,preview=True))
    assert r['preview']=='摘要预览' and r['sent'] is False and len(seen)==1
    with session_scope() as db:
        assert db.get(ConsoleState,'ai_summary_last_run') is None
        assert db.query(AiSummaryRun).one().status=='preview'


def test_count_threshold_independent_of_summary_limit(monkeypatch):
    ai_seed('count')
    monkeypatch.setattr('app.relay_engine.request_summary',lambda *args:'摘要')
    engine=RelayEngine();engine.running=True
    send=AsyncMock(return_value=SimpleNamespace(id=1))
    engine.workers[1]=SimpleNamespace(client=SimpleNamespace(is_connected=lambda:True,send_message=send))
    r=asyncio.run(engine.generate_ai_summary())
    assert r['sent'] and r['message_count']==1 and send.await_count==1
    assert not asyncio.run(engine.generate_ai_summary())['sent']


def test_ai_connection_test_does_not_read_chat_or_send(monkeypatch):
    ai_seed()
    seen=[]
    monkeypatch.setattr('app.ai_summary.request_summary',lambda u,k,m,p:seen.extend(p) or '连接成功')
    with TestClient(app) as c:
        login(c)
        r=c.post('/api/ai/test',headers=HEAD,json={'api_url':'https://ai.example/v1/chat/completions','model':'test'})
        assert r.status_code==200 and seen[0]['chat_title']=='连接测试'
        assert c.post('/api/ai/test',headers=HEAD,json={'api_url':'https://different.example/api','model':'test'}).status_code==422
        assert c.get('/api/ai/activity').json()['collected']==2


def test_config_change_during_ai_generation_prevents_send(monkeypatch):
    ai_seed()
    def change(*args):
        with session_scope() as db:
            row=db.get(ConsoleState,'ai_summary_settings'); value=json.loads(row.value); value['enabled']=False;row.value=json.dumps(value)
        return '摘要'
    monkeypatch.setattr('app.relay_engine.request_summary',change)
    engine=RelayEngine();engine.running=True
    send=AsyncMock()
    engine.workers[1]=SimpleNamespace(client=SimpleNamespace(is_connected=lambda:True,send_message=send))
    with pytest.raises(ValueError,match='配置已变更'):asyncio.run(engine.generate_ai_summary(force=True))
    assert not send.called
    with session_scope() as db: assert db.query(AiSummaryRun).one().status=='failed'


def test_ai_send_uncertainty_is_recorded_once_and_not_retried(monkeypatch):
    ai_seed('count')
    monkeypatch.setattr('app.relay_engine.request_summary', lambda *args: '摘要')
    engine=RelayEngine();engine.running=True
    send=AsyncMock(side_effect=TimeoutError())
    engine.workers[1]=SimpleNamespace(client=SimpleNamespace(is_connected=lambda:True,send_message=send))
    with pytest.raises(ValueError,match='待核实'):
        asyncio.run(engine.generate_ai_summary())
    assert not asyncio.run(engine.generate_ai_summary())['sent']
    with session_scope() as db:
        assert db.query(AiSummaryRun).count()==1
        assert db.query(AiSummaryRun).one().status=='unknown'
    assert send.await_count==1


def test_ai_snapshot_preserves_new_messages_arriving_during_generation(monkeypatch):
    ai_seed('count')
    def generate(*args):
        with session_scope() as db:
            for mid in [50,51]:
                db.add(AiSummaryMessage(account_id=1,chat_id=-1001,message_id=mid,chat_title='测试群',sender_name='用户',text='新消息'))
        return '摘要'
    monkeypatch.setattr('app.relay_engine.request_summary',generate)
    engine=RelayEngine();engine.running=True
    send=AsyncMock(return_value=SimpleNamespace(id=1))
    engine.workers[1]=SimpleNamespace(client=SimpleNamespace(is_connected=lambda:True,send_message=send))
    assert asyncio.run(engine.generate_ai_summary())['sent']
    monkeypatch.setattr('app.relay_engine.request_summary',lambda *args:'第二次摘要')
    assert asyncio.run(engine.generate_ai_summary())['sent']
    assert send.await_count==2


def test_workspace_additive_schema_is_repeatable():
    from app.database import init_db
    queued()
    with session_scope() as db:
        db.add(ConsoleState(key='ai_summary_settings',value='{"model":"saved"}'))
    init_db();init_db()
    with session_scope() as db:
        assert db.get(BroadcastTask,1).status=='queued'
        assert db.query(BroadcastDelivery).count()==1
        assert json.loads(db.get(ConsoleState,'ai_summary_settings').value)['model']=='saved'
