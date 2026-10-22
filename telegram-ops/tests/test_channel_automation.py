import asyncio
import json
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from telethon.tl.types import Channel
from telethon.errors import FloodWaitError
from app.main import app
from app.database import session_scope
from app.models import Account
from app.channel_models import ChannelFeed, ChannelDraft
from app.channel_format import format_channel_post, source_link, split_post, units
from app.channel_rules import prepare_post
from app.channel_service import ingest, collect_feed, publish_next
from app.schema_updates import add_channel_automation
from app.relay_engine import manager
from test_console_api import login, HEAD
from test_channel_editor import fixture_feed, fake_engine, message, make_draft, queue_draft, SOURCE, TARGET


def enable_auto(fid=1,target=TARGET):
    with session_scope() as db:
        f=db.get(ChannelFeed,fid);f.auto_publish=True;f.target_id=target


def auto_draft(text='付款后未收到约定服务',media=False):
    fid=fixture_feed();enable_auto(fid)
    with session_scope() as db:
        rows=[message(11,text,3,'photo'),message(12,'',3,'video')] if media else [message(11,text)]
        ingest(db,db.get(ChannelFeed,fid),rows);db.flush();return db.query(ChannelDraft).one().id


def test_template_source_fields_and_escaping_without_inferred_amounts():
    source='付款 300u 后未交付，之后又要求 100u。\n重复提醒\n重复提醒\n\n👤\n123456789\n👦🏻 示例用户\n🌐 @example_person\n🇨🇳 Chinese\n🕑 最後同步時間 01/01/2026\n\n👤 987654321\n👦🏻 第二个用户\n🌐 @second_person\n<script>alert(1)</script>'
    markup=format_channel_post(source,'曝光档案',2,datetime(2026,10,22,16,1),'https://t.me/qt00/11',True)
    text,entities,_=prepare_post(markup,allow_split=True)
    assert '2026.10.23' in text and '#0002' not in text
    assert '300u' in text and '100u' in text and '涉及金额' not in text
    assert text.count('重复提醒')==1 and 'Chinese' not in text and '最後同步時間' not in text
    assert 'Telegram ID：123456789' in text and '@example_person' in text and '@second_person' in text
    assert '<script>alert(1)</script>' in text and '<script>' not in markup
    assert '原帖附件已保留' not in text and '查看原始信息' not in text


def test_labeled_fields_keep_original_unit_and_hide_missing_fields():
    markup=format_channel_post('涉及金额：500 元\n交易时间：2026-10-01\n骗子地址：T'+'a'*33,'档案',1,datetime.utcnow(),'https://t.me/qt00/1')
    text,_,_=prepare_post(markup,allow_split=True)
    assert '500 元' in text and 'USDT' not in text and '交易时间：2026-10-01' in text
    assert '涉及账号' not in text and '相关地址' in text


def test_split_keeps_all_emoji_and_entity_offsets():
    markup='<b>'+('中😀文'*2500)+'</b>\n<a href="https://example.com/proof">'+('证据😊'*1800)+'</a>'
    text,entities,_=prepare_post(markup,allow_split=True)
    parts=list(split_post(text,entities))
    assert len(parts)>2 and ''.join(t for t,_ in parts)==text
    assert all(units(t)<=4096 for t,_ in parts)
    for value,items in parts:
        for e in items:assert 0<=e.offset<e.offset+e.length<=units(value)
        value.encode('utf-8')


def test_new_auto_post_is_formatted_queued_and_sent_without_manual_action():
    did=auto_draft('交易记录\n👤 123456789\n👦🏻 示例\n🌐 @example_person')
    with session_scope() as db:
        d=db.get(ChannelDraft,did);assert d.status=='queued' and d.automatic and d.publish_target==TARGET
        assert '<b>事件经过</b>' in d.body_html
    e,c=fake_engine();asyncio.run(publish_next(e,1));asyncio.run(publish_next(e,1))
    assert c.send_message.await_count==1
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='sent'


def test_filters_run_before_format_and_queue_even_for_album_caption():
    fid=fixture_feed();enable_auto(fid)
    with session_scope() as db:
        f=db.get(ChannelFeed,fid);ingest(db,f,[message(11,'普通正文',5,'photo'),message(12,'七天相关',5,'photo'),message(13,'广告合作')])
    with session_scope() as db:
        assert all(d.status=='filtered' and not d.automatic for d in db.query(ChannelDraft))
        assert db.get(ChannelFeed,fid).cursor==13


def test_target_missing_retains_formatted_post_and_never_autoreplays_it():
    fid=fixture_feed();enable_auto(fid,None)
    with session_scope() as db:
        f=db.get(ChannelFeed,fid);ingest(db,f,[message(11,'原帖')])
        db.flush();d=db.query(ChannelDraft).one();assert d.status=='draft' and d.automatic and '<b>曝光档案' in d.body_html
        f.target_id=TARGET;ingest(db,f,[message(11,'原帖')])
        assert d.status=='draft' and d.publish_target is None
    e,c=fake_engine();asyncio.run(publish_next(e,1));c.send_message.assert_not_awaited()


def test_repeated_collection_preserves_one_automatic_job():
    fid=fixture_feed();enable_auto(fid)
    with session_scope() as db:
        f=db.get(ChannelFeed,fid);ingest(db,f,[message(11,'原帖')]);f.cursor=10;ingest(db,f,[message(11,'原帖')])
    with session_scope() as db:assert db.query(ChannelDraft).count()==1


def test_pause_cancels_automatic_jobs_without_sending():
    did=auto_draft()
    with TestClient(app) as c:
        login(c);assert c.post('/api/channel-feeds/1/pause',headers=HEAD).status_code==200
    e,client=fake_engine();asyncio.run(publish_next(e,1));client.send_message.assert_not_awaited()
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='draft'


def test_automatic_mode_disabled_during_resolve_prevents_send():
    did=auto_draft();e,c=fake_engine()
    async def target(*a,**k):
        with session_scope() as db:db.get(ChannelFeed,1).auto_publish=False
        return NS(broadcast=True,creator=True)
    c.get_entity.side_effect=target;asyncio.run(publish_next(e,1));c.send_message.assert_not_awaited()
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='draft'


def test_long_album_sends_attachment_then_all_formatted_text():
    did=auto_draft('交易经过😀\n'+'完整内容保持不变😀'*800,True);e,c=fake_engine()
    c.get_messages.return_value=[message(11,kind='photo'),message(12,kind='video')]
    with session_scope() as db:text,_,_=prepare_post(db.get(ChannelDraft,did).body_html,allow_split=True)
    asyncio.run(publish_next(e,1))
    assert c.send_file.await_count==1 and c.send_message.await_count>=2
    assert units(c.send_file.call_args.kwargs['caption'][0])<=1024
    assert ''.join(call.args[1] for call in c.send_message.call_args_list)==text
    assert all(units(call.args[1])<=4096 for call in c.send_message.call_args_list)
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='sent'


def test_partial_timeout_preserves_known_sent_ids_and_stops_retry():
    did=auto_draft('完整记录'*400,True);e,c=fake_engine();c.get_messages.return_value=[message(11,kind='photo'),message(12,kind='photo')]
    c.send_message.side_effect=TimeoutError()
    asyncio.run(publish_next(e,1));asyncio.run(publish_next(e,1))
    assert c.send_file.await_count==1 and c.send_message.await_count==1
    with session_scope() as db:
        d=db.get(ChannelDraft,did);assert d.status=='unknown' and json.loads(d.sent_ids)==[701,702]


def test_pause_between_attachment_and_body_keeps_partial_status():
    did=auto_draft('完整记录'*400,True);e,c=fake_engine();c.get_messages.return_value=[message(11,kind='photo'),message(12,kind='photo')]
    async def delivered(*a,**k):
        with session_scope() as db:db.get(ChannelFeed,1).enabled=False
        return [NS(id=800),NS(id=801)]
    c.send_file.side_effect=delivered;asyncio.run(publish_next(e,1));c.send_message.assert_not_awaited()
    with session_scope() as db:
        d=db.get(ChannelDraft,did);assert d.status=='unknown' and json.loads(d.sent_ids)==[800,801]


def test_first_single_send_waits_and_preserves_queue():
    did=auto_draft();e,c=fake_engine();c.send_message.side_effect=FloodWaitError(request=None,capture=30)
    asyncio.run(publish_next(e,1));asyncio.run(publish_next(e,1))
    assert c.send_message.await_count==1
    with session_scope() as db:assert db.get(ChannelDraft,did).status=='waiting'


def test_enabling_auto_verifies_target_permissions_and_no_send():
    fid=fixture_feed(False);enable_auto(fid);e,client=fake_engine()
    channel=Channel(id=123,title='source',photo=None,date=datetime.utcnow(),access_hash=12,broadcast=True)
    with TestClient(app) as c:
        login(c);manager.running=True;manager.workers[1]=NS(client=client)
        try:
            client.get_entity.side_effect=[channel,NS(broadcast=True,creator=False,admin_rights=None)]
            assert c.post(f'/api/channel-feeds/{fid}/start',headers=HEAD).status_code==422
            client.get_entity.side_effect=[channel,NS(broadcast=True,creator=True)]
            client.get_messages.return_value=[message(20)]
            assert c.post(f'/api/channel-feeds/{fid}/start',headers=HEAD).status_code==200
            client.send_message.assert_not_awaited();client.send_file.assert_not_awaited()
        finally:manager.running=False;manager.workers.clear()


def test_layout_preview_does_not_change_or_publish_and_format_existing_is_manual():
    did=make_draft()
    with TestClient(app) as c:
        login(c)
        r=c.post('/api/channel-layout/preview',json=dict(text='原始经过\n👤 123456789\n👦🏻 用户\n🌐 @example_person',template_title='自定义档案'),headers=HEAD)
        assert r.status_code==200 and '自定义档案' in r.json()['text']
        d=c.post(f'/api/channel-drafts/{did}/format',json=dict(version=1),headers=HEAD).json()
        assert d['status']=='draft' and not d['automatic'] and '<b>事件经过</b>' in d['body_html']
    with session_scope() as db:assert db.query(ChannelDraft).count()==1


def test_configuration_only_affects_new_posts_and_validates_title():
    did=make_draft()
    with TestClient(app) as c:
        login(c);data=dict(name='采集',account_id=1,source='qt00',target_id=TARGET,auto_publish=True,template_title='我的档案')
        assert c.put('/api/channel-feeds/1',json=data,headers=HEAD).status_code==200
        assert c.put('/api/channel-feeds/1',json=data|dict(auto_publish='yes'),headers=HEAD).status_code==422
        assert c.put('/api/channel-feeds/1',json=data|dict(template_title='   '),headers=HEAD).status_code==422
    with session_scope() as db:
        d=db.get(ChannelDraft,did);assert not d.automatic and d.status=='draft'
        assert not db.get(ChannelFeed,1).enabled


def test_migration_is_repeatable_preserves_old_rows_and_manual_default(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with engine.begin() as c:
        c.exec_driver_sql('create table channel_feeds (id integer primary key, cursor integer, enabled boolean)')
        c.exec_driver_sql('create table channel_drafts (id integer primary key, status text, body_html text)')
        c.exec_driver_sql("insert into channel_feeds values (1,1234,1)")
        c.exec_driver_sql("insert into channel_drafts values (1,'draft','original')")
    add_channel_automation(engine);add_channel_automation(engine)
    with engine.connect() as c:
        assert c.exec_driver_sql('select cursor,enabled,auto_publish,template_title from channel_feeds').one()==(1234,1,0,'曝光档案')
        assert c.exec_driver_sql('select status,body_html,automatic,config_revision from channel_drafts').one()==('draft','original',0,None)
    engine.dispose()


def test_legacy_config_client_does_not_reset_saved_automation():
    fid=fixture_feed();enable_auto(fid)
    with TestClient(app) as c:
        login(c)
        assert c.put('/api/channel-feeds/1',json=dict(name='旧客户端改名',source='qt00',account_id=1,target_id=TARGET),headers=HEAD).status_code==200
    with session_scope() as db:assert db.get(ChannelFeed,fid).auto_publish is True
