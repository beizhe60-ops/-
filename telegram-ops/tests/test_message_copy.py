import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from telethon.tl.types import MessageEntityBold, MessageEntityTextUrl
from app.main import app
from app.message_copy import prepare_copy, normalize_image
from app.database import session_scope
from app.relay_models import MessageAsset, SenderBinding, RelayJob
from app.schema_updates import add_message_copy_options
from test_console_api import login, HEAD, seed as api_seed
from test_relay import seed, engine, dm


def picture():
    buf=io.BytesIO();Image.new('RGB',(120,80),'teal').save(buf,'PNG');return buf.getvalue()


def test_formatting_emoji_and_links():
    text,entities,markup=prepare_copy('😊 <b>你好</b> <a href="https://example.com/?a=1&amp;b=2">查看详情</a>',{'format':'html'})
    assert text=='😊 你好 查看详情'
    bold=next(e for e in entities if isinstance(e,MessageEntityBold))
    assert bold.offset==3 and bold.length==2
    assert next(e for e in entities if isinstance(e,MessageEntityTextUrl)).url=='https://example.com/?a=1&b=2'
    assert prepare_copy('<b>原文</b>',{})[0]=='<b>原文</b>'


@pytest.mark.parametrize('source', ['<script>alert(1)</script>','<img src=x onerror=x>','<b onclick="x">hi</b>','<a href="javascript:alert(1)">hi</a>','<a href="https://name:secret@example.com">hi</a>','<b>hi','<b><i>hi</b></i>'])
def test_invalid_format_rejected(source):
    with pytest.raises(ValueError):prepare_copy(source,{'format':'html'})


def test_caption_limit_and_invalid_images():
    with pytest.raises(ValueError,match='1024'):prepare_copy('😊'*513,{'image_id':'a'*64})
    assert len(prepare_copy('a'*1024,{'image_id':'a'*64})[0])==1024
    for data in [b'<svg/>',b'not image',b'a'*(5*1024*1024+1)]:
        with pytest.raises(ValueError):normalize_image(data)


def test_upload_preview_save_and_configuration_change():
    api_seed()
    with TestClient(app) as c:
        assert c.get('/api/message-assets/nope').status_code==401
        login(c)
        assert c.post('/api/message-assets',files={'file':('a.png',picture(),'image/png')}).status_code==403
        r=c.post('/api/message-assets',headers=HEAD,files={'file':('a.png',picture(),'image/png')})
        assert r.status_code==200
        asset=r.json()['id']
        assert c.get(r.json()['url']).headers['content-type']=='image/jpeg'
        assert c.post('/api/message-assets',headers=HEAD,files={'file':('a.png',picture(),'image/png')}).json()['id']==asset
        payload={'template':'🎉 <b>欢迎 {{ username }}</b>','format':'html','image_id':asset}
        preview=c.post('/api/message-copy/preview',headers=HEAD,json=payload)
        assert preview.status_code==200 and '<b>' in preview.json()['html']
        assert c.put('/api/accounts/2/receive-groups',headers=HEAD,json={**payload,'chat_ids':[-1002]}).status_code==200
        with session_scope() as db:
            assert db.query(MessageAsset).count()==1
            assert db.query(RelayJob).count()==0
            assert json.loads(db.get(SenderBinding,2).copy_options)['image_id']==asset
        # Unknown image IDs never become queued attachments.
        assert c.put('/api/accounts/2/receive-groups',headers=HEAD,json={**payload,'image_id':'f'*64,'chat_ids':[-1002]}).status_code==422


def test_rich_image_send_uses_one_message_and_snapshotted_options():
    task=seed();task.template=''
    data,_,_=normalize_image(picture())
    with session_scope() as db:
        from app.relay_models import RelayTask
        db.get(RelayTask,task.id).template=''
        db.add(MessageAsset(id='a'*64,data=data,content_type='image/jpeg'))
        binding=db.get(SenderBinding,2)
        binding.template='😊 <b>欢迎 {{ username }}</b> <a href="https://example.com">详情</a>'
        binding.copy_options=json.dumps({'format':'html','image_id':'a'*64})
    e=engine();client=e.workers[2].client
    client.send_file=AsyncMock(return_value=SimpleNamespace(id=701))
    dm(e,task)
    with session_scope() as db:
        j=db.query(RelayJob).one();assert json.loads(j.copy_options)['image_id']=='a'*64
    asyncio.run(e.process_pending_queue())
    client.send_message.assert_not_awaited();client.send_file.assert_awaited_once()
    kwargs=client.send_file.call_args.kwargs
    assert kwargs['caption']=='😊 欢迎 @target_user 详情'
    assert kwargs['parse_mode'] is None and any(isinstance(x,MessageEntityTextUrl) for x in kwargs['formatting_entities'])
    with session_scope() as db:assert db.query(RelayJob).one().status=='sent'


def test_image_change_cancels_pending_copy_but_keeps_history():
    task=seed();task.template=''
    e=engine()
    from app.relay_models import RelayTask
    with session_scope() as db:db.get(RelayTask,task.id).template=''
    dm(e,task)
    with TestClient(app) as c:
        login(c)
        assert c.put('/api/accounts/2/receive-groups',headers=HEAD,json={'chat_ids':[-1002],'template':'<b>新文案</b>','format':'html'}).status_code==200
    with session_scope() as db:assert db.query(RelayJob).one().status=='cancelled'


def test_copy_columns_migrate_idempotently(tmp_path):
    e=create_engine('sqlite:///'+str(tmp_path/'old.db'))
    with e.begin() as db:
        for t in ['sender_bindings','relay_jobs']:
            db.exec_driver_sql(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, template TEXT)')
            db.exec_driver_sql(f"INSERT INTO {t} VALUES (1,'old')")
    add_message_copy_options(e);add_message_copy_options(e)
    with e.connect() as db:
        for t in ['sender_bindings','relay_jobs']:
            assert db.exec_driver_sql(f'SELECT template,copy_options FROM {t}').one()==('old','{}')
    e.dispose()
