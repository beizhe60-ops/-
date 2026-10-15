import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from app.main import app
from app.crypto import encrypt_text
from app.database import session_scope
from app.models import Account
from app.relay_models import RelayTask, RelayJob, ConsoleState, ContactReceipt
from app.relay_engine import RelayEngine
from app.schema_updates import add_task_deletion_marker
from test_console_api import seed, login, HEAD


def task_seed():
    seed()
    with session_scope() as db:
        db.get(Account,1).session_string_encrypted=encrypt_text('fixture-session')
        for id in [1,2]:
            db.add(RelayTask(id=id,name=f'T{id}',account_a=id,account_b=id,source_chats='[-1001]',relay_chat=-1002,keywords='test',template='',enabled=True))
        db.flush()
        for id,status in enumerate(['pending','waiting','sent','unknown','sending'],1):
            db.add(RelayJob(id=id,task_id=1,account_id=1,stage='relay',chat_id=-1001,message_id=id,username='test_user',original_text='fixture',text='fixture',status=status))
        db.add(RelayJob(id=6,task_id=2,account_id=2,stage='relay',chat_id=-1001,message_id=6,username='test_user',original_text='fixture',text='fixture'))
        db.flush()
        db.add(ContactReceipt(user_id=42,job_id=3))


def isolated_manager(monkeypatch):
    engine=RelayEngine();engine.running=True
    one=SimpleNamespace(disconnect=AsyncMock(),is_connected=lambda:True)
    two=SimpleNamespace(disconnect=AsyncMock(),is_connected=lambda:True)
    engine.workers={1:SimpleNamespace(client=one),2:SimpleNamespace(client=two)}
    engine.identities={1:101,2:102}
    monkeypatch.setattr('app.console_api.manager',engine)
    client=SimpleNamespace(connect=AsyncMock(),disconnect=AsyncMock(),is_user_authorized=AsyncMock(return_value=True),log_out=AsyncMock(return_value=True))
    factory=Mock(return_value=client)
    monkeypatch.setattr('app.console_api.make_client',factory)
    return engine,one,two,client,factory


def test_logout_only_disconnects_selected_account_and_keeps_tasks(monkeypatch):
    task_seed()
    engine,one,two,client,factory=isolated_manager(monkeypatch)
    with TestClient(app) as c:
        login(c)
        assert c.post('/api/accounts/1/logout',headers=HEAD).status_code==200
        assert engine.running and 2 in engine.workers and 1 not in engine.workers
        one.disconnect.assert_awaited_once();two.disconnect.assert_not_awaited()
        client.log_out.assert_awaited_once();client.disconnect.assert_awaited_once()
        with session_scope() as db:
            assert db.get(Account,1).session_string_encrypted is None
            assert db.get(Account,1).status=='login_required'
            assert db.get(Account,2).status=='active'
            assert all(t.enabled for t in db.query(RelayTask))
            assert db.get(RelayJob,1).status=='pending'
            assert db.get(RelayJob,6).status=='pending'
        assert c.post('/api/accounts/1/logout',headers=HEAD).status_code==200
        assert factory.call_count==1


def test_logout_failure_keeps_other_accounts_running_and_can_retry(monkeypatch):
    task_seed()
    engine,one,two,client,factory=isolated_manager(monkeypatch)
    client.connect.side_effect=TimeoutError('secret-do-not-expose')
    with TestClient(app) as c:
        login(c)
        r=c.post('/api/accounts/1/logout',headers=HEAD)
        assert r.status_code==400 and 'secret-do-not-expose' not in r.text
        assert engine.running and 2 in engine.workers
        two.disconnect.assert_not_awaited()
        with session_scope() as db:
            assert db.get(Account,1).session_string_encrypted
            assert db.get(Account,1).status=='login_required'
        client.connect.side_effect=None
        client.is_user_authorized.return_value=False
        assert c.post('/api/accounts/1/logout',headers=HEAD).status_code==200
        client.log_out.assert_not_awaited()


def test_account_logout_waits_only_for_its_account(monkeypatch):
    task_seed()
    engine,one,two,client,factory=isolated_manager(monkeypatch)
    from app.console_api import logout_account
    async def run():
        async with engine.account_lock(1):
            with session_scope() as db:
                work=asyncio.create_task(logout_account(1,db))
                await asyncio.sleep(0)
                assert not work.done()
                async with engine.account_lock(2):
                    assert engine.running and 2 in engine.workers
        await work
    asyncio.run(run())


def test_delete_binding_preserves_history_and_unrelated_task(monkeypatch):
    task_seed()
    engine,*_=isolated_manager(monkeypatch)
    with TestClient(app) as c:
        assert c.delete('/api/tasks/1',headers=HEAD).status_code==401
        login(c)
        assert c.delete('/api/tasks/1').status_code==403
        for _ in range(2):assert c.delete('/api/tasks/1',headers=HEAD).status_code==200
        assert [t['id'] for t in c.get('/api/state').json()['tasks']]==[2]
        assert c.post('/api/tasks/1/toggle',headers=HEAD).status_code==404
        payload=dict(name='restore',account_a=1,source_chats=[-1001],relay_chat=-1002,keywords='test')
        assert c.put('/api/tasks/1',headers=HEAD,json=payload).status_code==404
        with session_scope() as db:
            task=db.get(RelayTask,1)
            assert not task.enabled and task.deleted_at
            assert db.get(RelayTask,2).enabled
            assert [db.get(RelayJob,i).status for i in range(1,7)]==['cancelled','cancelled','sent','unknown','sending','pending']
            assert db.get(ContactReceipt,42).job_id==3
            assert db.query(RelayJob).count()==6
            snapshot=task
        engine.enqueue(snapshot,'relay',1,-1001,99,42,'test_user','group','test','test')
        with session_scope() as db:assert db.query(RelayJob).count()==6
        assert engine.running


def test_admin_web_logout_does_not_change_running_state():
    with TestClient(app) as c:
        login(c)
        with session_scope() as db:db.add(ConsoleState(key='running',value='true'))
        assert c.get('/admin/logout',follow_redirects=False).status_code==303
        assert c.get('/api/state').status_code==401
        with session_scope() as db:assert db.get(ConsoleState,'running').value=='true'


def test_task_marker_migrates_existing_rows_idempotently(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'old.db'))
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE relay_tasks (id INTEGER PRIMARY KEY, enabled BOOLEAN NOT NULL)')
        db.exec_driver_sql('INSERT INTO relay_tasks VALUES (1,1)')
    add_task_deletion_marker(engine);add_task_deletion_marker(engine)
    with engine.connect() as db:
        assert db.exec_driver_sql('SELECT id,enabled,deleted_at FROM relay_tasks').one()==(1,1,None)
        assert [c['name'] for c in inspect(db).get_columns('relay_tasks')].count('deleted_at')==1
    engine.dispose()
