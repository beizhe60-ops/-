import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel
from app.main import app
from app.database import session_scope, init_db
from app.models import Account
from app.channel_models import ChannelFeed, ChannelDraft
from app.channel_rules import filter_post, prepare_post
from app.channel_service import ingest, collect_feed, publish_next, recover_channel_posts
from app.relay_engine import RelayEngine, manager
from test_console_api import seed, login, HEAD

SOURCE=-1000000000123
TARGET=-1000000000456


def message(mid,text='',album=None,kind=None):
    return NS(id=mid,raw_text=text,grouped_id=album,date=datetime.now(timezone.utc)-timedelta(seconds=10),
        photo=object() if kind=='photo' else None,video=object() if kind=='video' else None,
        document=object() if kind=='document' else None,media=object() if kind else None,action=None)


def fixture_feed(enabled=True):
    seed()
    with session_scope() as db:
        f=ChannelFeed(name='曝光采编',account_id=1,source='qt00',source_id=SOURCE,source_hash='12',source_title='七天担保曝光频道',target_id=TARGET,enabled=enabled,cursor=10)
        db.add(f);db.flush();return f.id


def fake_engine():
    e=RelayEngine();e.running=True
    client=NS(is_connected=lambda:True,get_messages=AsyncMock(return_value=[]),get_entity=AsyncMock(return_value=NS(broadcast=True,creator=True)),send_message=AsyncMock(return_value=NS(id=700)),send_file=AsyncMock(return_value=[NS(id=701),NS(id=702)]))
    e.workers[1]=NS(client=client);return e,client


def make_draft(media=False):
    fid=fixture_feed()
    with session_scope() as db:
        rows=[message(11,'有损失的交易信息',album=3,kind='photo'),message(12,album=3,kind='video')] if media else [message(11,'有损失的交易信息')]
        ingest(db,db.get(ChannelFeed,fid),rows);db.flush();return db.query(ChannelDraft).one().id


def queue_draft(did):
    with session_scope() as db:
        d=db.get(ChannelDraft,did);d.status='queued';d.publish_target=TARGET


def test_rules_exclude_whole_post_and_avoid_channel_title_or_arbitrary_links():
    assert filter_post('曝光用户 @example_id，证据 https://t.me/example/1','七天',True,'广告合作') is None
    assert filter_post('七天相关信息','七天',True,'广告合作')=='命中排除词：七天'
    assert filter_post('本帖广告合作','七天',True,'广告合作').startswith('广告规则')
    assert filter_post('【广告】今天推广','七天',True,'')
    assert filter_post('【广告】今天推广','七天',False,'') is None


def test_ingest_albums_filtered_once_and_preserve_edits():
    fid=fixture_feed()
    with session_scope() as db:
        f=db.get(ChannelFeed,fid);ingest(db,f,[message(11,'交易损失',3,'photo'),message(12,'七天',3,'video'),message(13,'广告合作'),message(14,'正常曝光')])
    with session_scope() as db:
        rows=db.query(ChannelDraft).order_by(ChannelDraft.id).all()
        assert [r.status for r in rows]==['filtered','filtered','draft']
        assert json.loads(rows[0].message_ids)==[11,12] and len(json.loads(rows[0].media))==2
        rows[-1].body_html='<b>人工编辑内容</b>';f=db.get(ChannelFeed,fid);f.cursor=10;ingest(db,f,[message(14,'正常曝光')])
    with session_scope() as db:
        assert db.query(ChannelDraft).count()==3
        assert db.query(ChannelDraft).order_by(ChannelDraft.id.desc()).first().body_html=='<b>人工编辑内容</b>'
        assert db.get(ChannelFeed,fid).cursor==14


def test_collect_respects_cursor_filters_and_no_sends():
    fid=fixture_feed();e,c=fake_engine();c.get_messages.return_value=[message(11,'普通曝光'),message(12,'七天曝光')]
    asyncio.run(collect_feed(e,fid));asyncio.run(collect_feed(e,fid))
    with session_scope() as db:
        assert [r.status for r in db.query(ChannelDraft)]==['draft','filtered']
        assert db.get(ChannelFeed,fid).cursor==12
    assert c.get_messages.call_args.kwargs['min_id']==12
    c.send_message.assert_not_called();c.send_file.assert_not_called()


def test_album_page_boundary_is_collected_as_one_post():
    fid=fixture_feed();e,c=fake_engine();c.get_messages.side_effect=[[message(11,'证据',8,'photo')],[message(12,'补充说明',8,'photo'),message(13,'下一帖')]]
    asyncio.run(collect_feed(e,fid))
    with session_scope() as db:
        assert json.loads(db.query(ChannelDraft).one().message_ids)==[11,12]
        assert db.get(ChannelFeed,fid).cursor==12


def test_collection_pause_race_wait_and_retry_cursor():
    fid=fixture_feed();e,c=fake_engine()
    async def paused(*args,**kw):
        with session_scope() as db: db.get(ChannelFeed,fid).enabled=False
        return [message(11,'信息')]
    c.get_messages.side_effect=paused;asyncio.run(collect_feed(e,fid))
    with session_scope() as db:
        assert db.query(ChannelDraft).count()==0 and db.get(ChannelFeed,fid).cursor==10
        db.get(ChannelFeed,fid).enabled=True
    c.get_messages.side_effect=TimeoutError();asyncio.run(collect_feed(e,fid))
    with session_scope() as db:assert db.get(ChannelFeed,fid).cursor==10
    c.get_messages.side_effect=FloodWaitError(request=None,capture=25);asyncio.run(collect_feed(e,fid))
    with session_scope() as db:
        assert db.get(Account,1).flood_wait_until>datetime.utcnow() and db.get(Account,2).status=='active'


def test_api_auth_settings_preview_and_validation():
    seed()
    with TestClient(app) as c:
        assert c.get('/api/channel-feeds').status_code==401
        login(c);assert c.get('/console/channels').status_code==200
        data=dict(name='曝光采集',source='https://t.me/qt00',account_id=1)
        assert c.post('/api/channel-feeds',json=data).status_code==403
        assert c.post('/api/channel-feeds',json=data|dict(target_id=True),headers=HEAD).status_code==422
        assert c.post('/api/channel-feeds',json=data|dict(source='https://evil.example/a'),headers=HEAD).status_code==422
        r=c.post('/api/channel-feeds',json=data,headers=HEAD).json()
        assert r['source']=='qt00' and not r['enabled'] and r['target_id'] is None and 'source_hash' not in r
        assert not c.post('/api/channel-filter/preview',json=dict(text='七天'),headers=HEAD).json()['accepted']
        assert c.get('/api/channel-drafts?status=not-a-status').status_code==422


def test_enabling_baselines_without_history_and_reenable_skips_paused_history():
    fid=fixture_feed(False)
    with TestClient(app) as c:
        login(c);e,client=fake_engine()
        client.get_entity.return_value=Channel(id=123,title='来源',photo=None,date=datetime.now(timezone.utc),access_hash=12,broadcast=True)
        client.get_messages.return_value=[message(100,'旧消息')]
        manager.running=True;manager.workers[1]=NS(client=client)
        try:
            for _ in range(2):assert c.post(f'/api/channel-feeds/{fid}/start',headers=HEAD).status_code==200
            assert client.get_messages.await_count==1
            with session_scope() as db:assert db.get(ChannelFeed,fid).cursor==100 and db.query(ChannelDraft).count()==0
            c.post(f'/api/channel-feeds/{fid}/pause',headers=HEAD);client.get_messages.return_value=[message(200)]
            assert c.post(f'/api/channel-feeds/{fid}/start',headers=HEAD).status_code==200
            with session_scope() as db:assert db.get(ChannelFeed,fid).cursor==200
        finally:manager.running=False;manager.workers.clear()


def test_edit_then_publish_queue_version_and_missing_target():
    did=make_draft()
    with TestClient(app) as c:
        login(c);url=f'/api/channel-drafts/{did}'
        assert c.post(url+'/publish',json=dict(version=1),headers=HEAD).json()['status']=='queued'
        assert c.put(url,json=dict(version=2,body_html='新内容'),headers=HEAD).status_code==409
        assert c.post(url+'/cancel',json=dict(version=2),headers=HEAD).status_code==200
        r=c.put(url,json=dict(version=3,body_html='<b>已编辑</b> ✅'),headers=HEAD)
        assert r.status_code==200 and r.json()['body_html']=='<b>已编辑</b> ✅'
        assert c.put(url,json=dict(version=3,body_html='旧编辑'),headers=HEAD).status_code==409
        assert c.put(url,json=dict(version=4,body_html='<img src="x">'),headers=HEAD).status_code==422
        with session_scope() as db:db.get(ChannelFeed,1).target_id=None
        assert c.post(url+'/publish',json=dict(version=4),headers=HEAD).status_code==422
        with session_scope() as db:db.get(ChannelDraft,did).status='filtered'
        assert c.post(url+'/publish',json=dict(version=4),headers=HEAD).status_code==409


def test_config_change_cancels_queue_and_blocks_source_account_switch():
    did=make_draft();queue_draft(did)
    with TestClient(app) as c:
        login(c);data=dict(name='更新设置',source='qt00',account_id=1,target_id=-1000000000789)
        assert c.put('/api/channel-feeds/1',json=data,headers=HEAD).status_code==200
        with session_scope() as db:assert db.get(ChannelDraft,did).status=='draft' and not db.get(ChannelFeed,1).enabled
        assert c.put('/api/channel-feeds/1',json=data|dict(source='other'),headers=HEAD).status_code==409
        assert c.put('/api/channel-feeds/1',json=data|dict(account_id=2),headers=HEAD).status_code==409


def test_send_text_exact_format_once():
    did=make_draft();queue_draft(did);e,c=fake_engine()
    with session_scope() as db:db.get(ChannelDraft,did).body_html='<b>交易记录</b>\n<a href="https://example.com/proof">证据</a>'
    asyncio.run(publish_next(e,1));asyncio.run(publish_next(e,1))
    assert c.send_message.await_count==1 and c.send_message.call_args.args[1]=='交易记录\n证据'
    assert len(c.send_message.call_args.kwargs['formatting_entities'])==2
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='sent' and json.loads(db.get(ChannelDraft,did).sent_ids)==[700]


def test_send_album_without_forward_header_and_caption_once():
    did=make_draft(True);queue_draft(did);e,c=fake_engine();c.get_messages.return_value=[message(11,'证据',3,'photo'),message(12,'',3,'video')]
    asyncio.run(publish_next(e,1))
    assert c.send_file.await_count==1 and len(c.send_file.call_args.args[1])==2
    assert c.send_file.call_args.kwargs['caption']==['有损失的交易信息','']
    with session_scope() as db:assert json.loads(db.get(ChannelDraft,did).sent_ids)==[701,702]


def test_send_change_during_resolution_prevents_publish():
    did=make_draft();queue_draft(did);e,c=fake_engine()
    async def resolve(*a,**kw):
        with session_scope() as db:db.get(ChannelFeed,1).revision+=1
        return NS(broadcast=True,creator=True)
    c.get_entity.side_effect=resolve;asyncio.run(publish_next(e,1));c.send_message.assert_not_awaited()
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='draft'


def test_missing_permissions_deleted_media_and_no_send():
    did=make_draft(True);queue_draft(did);e,c=fake_engine();c.get_entity.return_value=NS(broadcast=True,creator=False,admin_rights=None)
    asyncio.run(publish_next(e,1));c.send_file.assert_not_awaited()
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='failed'
    queue_draft(did);c.get_entity.return_value=NS(broadcast=True,creator=True);c.get_messages.return_value=[None]
    asyncio.run(publish_next(e,1));c.send_file.assert_not_awaited()
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='failed'


def test_uncertain_send_no_retry_and_restart_recovery():
    did=make_draft();queue_draft(did);e,c=fake_engine();c.send_message.side_effect=TimeoutError()
    asyncio.run(publish_next(e,1));asyncio.run(publish_next(e,1));assert c.send_message.await_count==1
    with session_scope() as db:
        assert db.get(ChannelDraft,did).status=='unknown';db.get(ChannelDraft,did).status='sending'
    recover_channel_posts()
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='unknown'


def test_album_wait_is_uncertain_and_stops_retries():
    did=make_draft(True);queue_draft(did);e,c=fake_engine();c.get_messages.return_value=[message(11,kind='photo'),message(12,kind='video')]
    c.send_file.side_effect=FloodWaitError(request=None,capture=30);asyncio.run(publish_next(e,1))
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='unknown'
    assert c.send_file.await_count==1


def test_length_and_additive_idempotent_migration():
    with pytest.raises(ValueError):prepare_post('x'*1025,True)
    with pytest.raises(ValueError):prepare_post('<a href="javascript:alert(1)">x</a>')
    assert prepare_post('',True)[0]==''
    did=make_draft();init_db();init_db()
    with session_scope() as db:
        assert db.get(ChannelDraft,did).original_text=='有损失的交易信息' and db.get(Account,1).session_string_encrypted=='test'
