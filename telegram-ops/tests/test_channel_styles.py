import asyncio
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from telethon.tl import types
from app.main import app
from app.database import session_scope
from app.channel_models import ChannelFeed, ChannelDraft
from app.channel_rules import prepare_post
from app.channel_format import format_channel_post, split_post, units
from app.channel_service import ingest, publish_next
from app.schema_updates import add_channel_styles
from test_console_api import login, HEAD
from test_channel_editor import fixture_feed, message, fake_engine, make_draft, TARGET


@pytest.mark.parametrize('style,icon',[('alert','⚠️'),('lively','🚨'),('minimal','曝光档案')])
@pytest.mark.parametrize('fmt,entity',[
    ('normal',None),('bold',types.MessageEntityBold),('italic',types.MessageEntityItalic),
    ('underline',types.MessageEntityUnderline),('strike',types.MessageEntityStrike),
    ('quote',types.MessageEntityBlockquote),('code',types.MessageEntityCode),('spoiler',types.MessageEntitySpoiler),
])
def test_styles_keep_source_text_and_remove_generated_footer(style,icon,fmt,entity):
    markup=format_channel_post('正文😀 https://example.com/evidence\n👤 123456789\n👦🏻 示例用户','曝光档案',99,datetime.utcnow(),'https://t.me/qt00/100',True,emoji_style=style,body_format=fmt)
    text,entities,_=prepare_post(markup)
    assert text.startswith(icon) and '#0099' not in text
    assert not any(x in text for x in ['────────────','原帖附件已保留','查看原始信息'])
    assert 'https://example.com/evidence' in text and 'Telegram ID：123456789' in text
    if entity:
        match=[e for e in entities if type(e) is entity and text.encode('utf-16-le')[e.offset*2:(e.offset+e.length)*2].decode('utf-16-le').startswith('正文😀')]
        assert len(match)==1


def test_nested_spoiler_and_quote_utf16_roundtrip_and_split():
    raw='<blockquote><b>😀 标题</b>\n<tg-spoiler>'+('隐藏😀'*1400)+'</tg-spoiler></blockquote>\n<del>删除线</del> <a href="https://example.com">链接</a>'
    text,entities,clean=prepare_post(raw,allow_split=True)
    assert clean==raw
    assert {type(e) for e in entities} >= {types.MessageEntitySpoiler,types.MessageEntityBlockquote,types.MessageEntityBold,types.MessageEntityStrike,types.MessageEntityTextUrl}
    parts=list(split_post(text,entities))
    assert ''.join(t for t,_ in parts)==text and len(parts)>1
    for t,es in parts:
        assert all(0<=e.offset<e.offset+e.length<=units(t) for e in es)
    assert prepare_post(clean,allow_split=True)[:2]==(text,entities)


@pytest.mark.parametrize('markup',[
    '<tg-spoiler onclick="alert(1)">x</tg-spoiler>', '<script>x</script>',
    '<blockquote style="color:red">x</blockquote>', '<tg-spoiler>x</blockquote>',
    '<a href="javascript:alert(1)">x</a>', '<code><b>mixed</b></code>',
    '<blockquote><blockquote>x</blockquote></blockquote>',
])
def test_unsupported_markup_rejected(markup):
    with pytest.raises(ValueError):prepare_post(markup)


def test_preferences_saved_used_in_new_posts_and_preserved_for_legacy_client():
    fid=fixture_feed()
    with TestClient(app) as c:
        login(c)
        data=dict(name='采集',account_id=1,source='qt00',target_id=TARGET,auto_publish=True,emoji_style='lively',body_format='quote')
        assert c.put(f'/api/channel-feeds/{fid}',json=data,headers=HEAD).status_code==200
        legacy=dict(name='采集',account_id=1,source='qt00',target_id=TARGET)
        assert c.put(f'/api/channel-feeds/{fid}',json=legacy,headers=HEAD).status_code==200
        invalid=c.put(f'/api/channel-feeds/{fid}',json=data|dict(body_format='javascript'),headers=HEAD)
        assert invalid.status_code==422
    with session_scope() as db:
        feed=db.get(ChannelFeed,fid);assert (feed.emoji_style,feed.body_format)==('lively','quote')
        feed.enabled=True;ingest(db,feed,[message(11,'事件正文😀')]);db.flush()
        d=db.query(ChannelDraft).one();assert d.status=='queued' and '<blockquote>事件正文😀</blockquote>' in d.body_html
    e,client=fake_engine();asyncio.run(publish_next(e,1))
    call=client.send_message.call_args
    assert '🚨 曝光档案' in call.args[1] and '#0001' not in call.args[1]
    assert any(isinstance(x,types.MessageEntityBlockquote) for x in call.kwargs['formatting_entities'])


def test_preview_and_draft_edit_preserve_formats_without_publication():
    did=make_draft()
    with TestClient(app) as c:
        login(c)
        sample=dict(text='测试正文😀',template_title='自定义',emoji_style='minimal',body_format='spoiler')
        result=c.post('/api/channel-layout/preview',json=sample,headers=HEAD).json()
        assert result['html'].startswith('<b>自定义</b>') and '<tg-spoiler>测试正文😀</tg-spoiler>' in result['html']
        markup='<blockquote><b>事件</b>\n原文</blockquote>\n<code>123456</code> <tg-spoiler>隐藏😀</tg-spoiler>\n<del>修订</del> 2026-10-23 12:30'
        saved=c.put(f'/api/channel-drafts/{did}',json=dict(version=1,body_html=markup,keep_media=False),headers=HEAD)
        assert saved.status_code==200 and saved.json()['body_html']==markup and saved.json()['status']=='draft'
        assert c.get(f'/api/channel-drafts/{did}').json()['body_html']==markup
    with session_scope() as db:
        assert db.query(ChannelDraft).count()==1 and db.get(ChannelDraft,did).status=='draft'


def test_migration_repeatable_preserves_enabled_cursor_and_saved_preferences(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with engine.begin() as c:
        c.exec_driver_sql('create table channel_feeds(id integer primary key, enabled boolean, cursor integer)')
        c.exec_driver_sql('insert into channel_feeds values (1,1,1234)')
    add_channel_styles(engine)
    with engine.begin() as c:
        assert c.exec_driver_sql('select enabled,cursor,emoji_style,body_format from channel_feeds').one()==(1,1234,'alert','normal')
        c.exec_driver_sql("update channel_feeds set emoji_style='minimal',body_format='quote'")
    add_channel_styles(engine)
    with engine.connect() as c:assert c.exec_driver_sql('select emoji_style,body_format from channel_feeds').one()==('minimal','quote')
    engine.dispose()
