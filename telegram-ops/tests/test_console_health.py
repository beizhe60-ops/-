import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.console_health import next_summary_at
from app.console_metrics import RequestMetrics
from app.database import session_scope
from app.main import app
from app.relay_engine import manager, RelayEngine
from app.relay_models import RelayTask, RelayJob, ConsoleState, BroadcastTask, BroadcastDelivery
from app.channel_models import ChannelFeed, ChannelDraft
from app.models import Account
from test_console_api import login, seed


def test_schedule_matches_worker_minute_and_consumed_daily_slot():
    config = {'enabled': True, 'trigger': 'schedule', 'schedule_time': '23:00'}
    current = datetime(2026, 10, 23, 23, 0, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
    assert next_summary_at(config, now=current).isoformat() == '2026-10-23T23:00:00+08:00'
    assert next_summary_at(config, '2026-10-23', current).day == 24
    assert next_summary_at(config, now=current+timedelta(minutes=1)).day == 24
    assert next_summary_at({**config, 'enabled': False}, now=current) is None
    assert next_summary_at({**config, 'trigger': 'count'}, now=current) is None
    assert next_summary_at({**config, 'trigger': 'manual'}, now=current) is None


def test_health_scope_status_history_and_read_only_projection(monkeypatch):
    with TestClient(app) as c:
        seed()
        with session_scope() as db:
            db.add_all([RelayTask(id=i, name=f'Task {i}', account_a=1, account_b=2,
                                 source_chats=json.dumps([-1001 if i == 1 else -1003]),
                                 relay_chat=-1002, keywords='test', template='', enabled=i == 1)
                        for i in (1, 2, 3)])
            db.flush()
            db.get(RelayTask, 3).deleted_at = datetime.utcnow()
            for i, (stage, status) in enumerate([('relay', 'sent'), ('dm', 'sent'), ('relay', 'failed'), ('dm', 'unknown')], 1):
                db.add(RelayJob(task_id=1, stage=stage, account_id=1, chat_id=-1001, message_id=i,
                                username='example', original_text='PRIVATE BODY', text='PRIVATE BODY',
                                status=status, sent_at=datetime(2026, 10, 20+i)))
        assert c.get('/api/overview').status_code == 401
        login(c)
        monkeypatch.setattr(manager, 'workers', {1: SimpleNamespace(client=SimpleNamespace(is_connected=lambda: True))})
        monkeypatch.setattr(manager, 'recent_receipts', {(1, -1001): '2026-10-23T00:00:00Z', (2, -1003): '2026-10-23T01:00:00Z'})
        monkeypatch.setattr(manager, 'running', True)
        result = c.get('/api/overview')
        assert result.status_code == 200 and 'app;dur=' in result.headers['server-timing']
        rows = [r for r in result.json()['tasks'] if r['kind'] == 'relay']
        assert len(rows) == 2
        active = next(r for r in rows if r['id'] == 1)
        paused = next(r for r in rows if r['id'] == 2)
        assert active['last_received'] == '2026-10-23T00:00:00Z'
        assert paused['last_received'] is None and paused['status'] == 'paused'
        assert active['last_success'].startswith('2026-10-21')  # ignore newer DM success
        assert (active['failed'], active['unknown']) == (1, 1)
        assert 'PRIVATE BODY' not in result.text and '+12345678901' not in result.text
        monkeypatch.setattr(manager, 'workers', {})
        assert next(r for r in c.get('/api/overview').json()['tasks'] if r['id'] == 1)['label'] == '账号未连接'
        monkeypatch.setattr(manager, 'running', False)
        assert next(r for r in c.get('/api/overview').json()['tasks'] if r['id'] == 1)['label'] == '服务已暂停'
        with session_scope() as db:
            assert db.get(RelayTask, 1).enabled is True and db.query(RelayJob).count() == 4


def test_channel_and_ai_status_and_server_pause(monkeypatch):
    with TestClient(app) as c:
        seed(); login(c)
        with session_scope() as db:
            db.get(Account, 1).send_enabled = False
            db.add(ChannelFeed(id=1, name='Feed', account_id=1, source='example', source_id=-1001,
                               auto_publish=False, enabled=True))
            db.add(ConsoleState(key='ai_summary_settings', value=json.dumps({'enabled': True,'trigger': 'schedule',
                'account_id': 1,'source_chat_ids':[-1001],'api_key_encrypted':'NEVER-EXPOSE','target_id':-1002,'schedule_time':'23:00'})))
        monkeypatch.setattr(manager, 'workers', {1: SimpleNamespace(client=SimpleNamespace(is_connected=lambda: True))})
        monkeypatch.setattr(manager, 'running', True)
        result=c.get('/api/overview').json()
        assert next(t for t in result['tasks'] if t['kind']=='channel')['status'] == 'active'
        ai=next(t for t in result['tasks'] if t['kind']=='ai')
        assert ai['label']=='发送权限关闭' and ai['next_at']
        assert 'NEVER-EXPOSE' not in json.dumps(result)
        with session_scope() as db: db.get(ChannelFeed,1).error='Original provider detail'
        assert next(t for t in c.get('/api/overview').json()['tasks'] if t['kind']=='channel')['label']=='最近采集异常'
        monkeypatch.setattr(manager,'running',False)
        assert next(t for t in c.get('/api/overview').json()['tasks'] if t['kind']=='ai')['next_at'] is None
        assert c.get('/api/ai/activity').json()['next_at'] is None


def test_metrics_bounds_errors_percentiles_and_expiry(monkeypatch):
    import app.console_metrics as module
    clock=[10]
    monkeypatch.setattr(module, 'monotonic', lambda:clock[0])
    store=RequestMetrics(capacity=3,window_seconds=30)
    for ms, status in [(1,200),(2,200),(3,503),(4,200)]:store.record('GET','/api/tasks/{id}',ms,status)
    store.record('GET','/api/overview',9,200)
    store.record('GET','/api/state',9,401)
    row=store.snapshot()['rows'][0]
    assert store.snapshot()['samples']==3 and row['errors']==1
    assert (row['p50_ms'],row['p95_ms'],row['max_ms'])==(3,4,4)
    clock[0]=41
    assert store.snapshot()['samples']==0


def test_http_metrics_strip_query_and_dynamic_id_and_do_not_track_auth():
    from app.console_metrics import metrics
    with TestClient(app) as c:
        with metrics.lock:metrics.samples.clear()
        c.get('/api/state?q=NEVER-EXPOSE')
        assert metrics.snapshot()['samples']==0
        seed();login(c)
        assert c.get('/api/accounts/987654/listener?q=NEVER-EXPOSE').status_code==404
        assert c.get('/api/records?period=invalid&q=NEVER-EXPOSE').status_code==422
        data=c.get('/api/overview').json()['performance']
        encoded=json.dumps(data)
        assert '987654' not in encoded and 'NEVER-EXPOSE' not in encoded and '?' not in encoded
        assert any(r['route']=='GET /api/accounts/{id}/listener' and r['errors']==1 for r in data['rows'])
        assert any(r['route']=='GET /api/records' and r['errors']==1 for r in data['rows'])


def test_receipts_bounded_and_private_messages_excluded():
    engine=RelayEngine()
    for i in range(1002):engine.note_message(1,SimpleNamespace(chat_id=-i,id=i,is_group=True),'received')
    engine.note_message(1,SimpleNamespace(chat_id=123,id=1003,is_group=False,is_channel=False),'received')
    assert len(engine.recent_receipts)==1000
    assert (1,0) not in engine.recent_receipts and (1,123) not in engine.recent_receipts
    assert len(engine.listener_diagnostics[1])==100


def test_broadcast_health_excludes_finished_tasks_and_uses_saved_delivery(monkeypatch):
    with TestClient(app) as c:
        seed();login(c)
        with session_scope() as db:
            for id,status in enumerate(['queued','paused','completed','cancelled','draft'],1):
                db.add(BroadcastTask(id=id,request_key=f'test-{id}',name=f'Broadcast {id}',account_id=1,
                                     targets='[-1001]',text='PRIVATE BROADCAST',status=status,
                                     scheduled_at=datetime.utcnow()+timedelta(hours=1)))
            db.flush()
            db.add(BroadcastDelivery(task_id=1,chat_id=-1001,status='sent',sent_at=datetime(2026,10,23)))
            db.add(BroadcastDelivery(task_id=1,chat_id=-1002,status='unknown'))
        monkeypatch.setattr(manager,'running',True)
        monkeypatch.setattr(manager,'workers',{1:SimpleNamespace(client=SimpleNamespace(is_connected=lambda:True))})
        result=c.get('/api/overview')
        rows=[r for r in result.json()['tasks'] if r['kind']=='broadcast']
        assert {r['id'] for r in rows}=={1,2}
        queued=next(r for r in rows if r['id']==1)
        assert queued['label']=='等待计划时间' and queued['exceptions']==1
        assert queued['last_success'].startswith('2026-10-23')
        assert next(r for r in rows if r['id']==2)['label']=='已暂停'
        assert 'PRIVATE BROADCAST' not in result.text
