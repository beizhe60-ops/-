from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.console_records import period_start
from app.database import session_scope
from app.main import app
from app.relay_models import RelayJob, RelayTask
from test_console_api import login, seed


def test_period_boundaries_are_beijing_calendar_day_and_rolling_week():
    assert period_start("today", datetime(2026, 10, 22, 16, 1)) == datetime(2026, 10, 22, 16)
    assert period_start("today", datetime(2026, 10, 22, 15, 59)) == datetime(2026, 10, 21, 16)
    assert period_start("7d", datetime(2026, 10, 23, 1)) == datetime(2026, 10, 16, 1)
    assert period_start("all") is None


def test_record_filters_preserve_history_and_distinguish_exception_results():
    with TestClient(app) as client:
        seed()
        now = datetime.utcnow()
        with session_scope() as db:
            for id in (1, 2):
                db.add(RelayTask(id=id, name=f"任务 {id}", account_a=1, account_b=2, source_chats="[-1001]",
                                 relay_chat=-1002, keywords="咨询", template="", enabled=False,
                                 deleted_at=now if id == 2 else None))
            db.flush()
            for i, (status, created, task) in enumerate([
                ("failed", now, 1), ("unknown", now, 1), ("failed", now-timedelta(days=8), 1),
                ("sent", now, 1), ("failed", now, 2),
            ], 1):
                db.add(RelayJob(id=i, task_id=task, stage="relay", account_id=1, chat_id=-1001,
                                message_id=i, username="example_user", original_text="原文", text="正文",
                                status=status, created_at=created))
        login(client)
        response=client.get('/api/records',params={'period':'7d','task_id':1,'status':'exceptions','q':'@example_'})
        assert response.status_code == 200
        assert {row['id'] for row in response.json()['items']} == {1, 2}
        assert response.json()['total'] == 2
        assert client.get('/api/records?status=unknown').json()['total'] == 1
        assert client.get('/api/records?status=failed').json()['total'] == 3
        assert client.get('/api/records?task_id=2').json()['items'][0]['id'] == 5
        assert any(t['id']==2 and t['deleted'] for t in response.json()['tasks'])
        assert client.get('/api/records?task_id=999').json()['total'] == 0
        stats=client.get('/api/state').json()['stats']
        assert stats['exceptions'] == {'failed':3,'unknown':1}
        assert stats['failed'] == 4  # Preserve the legacy aggregate.
        assert client.get('/api/records').json()['total'] == 5
        for query in ('period=wrong','task_id=-2','task_id=abc'):
            assert client.get('/api/records?'+query).status_code == 422
        with session_scope() as db:
            assert [j.status for j in db.query(RelayJob).order_by(RelayJob.id)] == ['failed','unknown','failed','sent','failed']


@pytest.mark.parametrize('reason', ['outgoing_message', 'empty_text', 'missing_target', 'outside_monitor_scope', 'service_paused'])
def test_listener_skip_reasons_are_metadata_only_and_do_not_enqueue(reason):
    import asyncio
    from telethon.tl.types import User
    from app.relay_models import AccountProfile
    from test_relay import seed as relay_seed, engine, event
    task=relay_seed()
    manager=engine()
    message=event(-1001,User(id=999,username='example_user'),'咨询 private-body')
    if reason=='outgoing_message': message.out=True
    if reason=='empty_text': message.raw_text=' '
    if reason=='service_paused': manager.running=False
    with session_scope() as db:
        if reason=='missing_target': db.get(RelayTask,task.id).relay_chat=None
        if reason=='outside_monitor_scope': db.get(AccountProfile,1).monitor_chat_ids='[]'
    asyncio.run(manager.on_message(1,message))
    diagnostic=manager.listener_status(1)
    assert diagnostic['events'][-1]['outcome'] == reason
    assert 'private-body' not in str(diagnostic) and 'example_user' not in str(diagnostic)
    with session_scope() as db:
        assert db.query(RelayJob).count() == 0
