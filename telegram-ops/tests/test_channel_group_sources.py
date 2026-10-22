"""Group curation uses the existing media pipeline, independently of monitor/DM rules."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from telethon.tl.types import Channel

from app.main import app
from app.database import session_scope
from app.channel_models import ChannelFeed, ChannelDraft
from app.channel_service import collect_feed, source_peer
from app.relay_engine import manager
from app.schema_updates import add_channel_include_terms
from test_channel_editor import fixture_feed, fake_engine, message, SOURCE
from test_console_api import login, HEAD


@pytest.mark.parametrize('kind', ['broadcast', 'megagroup'])
def test_start_then_collect_new_media_only_with_positive_filter(kind):
    fid = fixture_feed(False)
    with session_scope() as db:
        feed = db.get(ChannelFeed, fid)
        feed.include_terms, feed.exclude_terms = '采购\n供应', '公群'
        feed.target_id = None
    with TestClient(app) as c:
        login(c)
        engine, client = fake_engine()
        client.get_entity.return_value = Channel(
            id=123, title='测试供需来源', photo=None, date=datetime.now(timezone.utc),
            access_hash=12, **{kind: True}
        )
        client.get_messages.return_value = [message(100, '不采集旧内容')]
        manager.running = True
        manager.workers[1] = NS(client=client)
        try:
            assert c.post(f'/api/channel-feeds/{fid}/start', headers=HEAD).status_code == 200
            with session_scope() as db:
                feed = db.get(ChannelFeed, fid)
                assert feed.cursor == 100 and feed.source_id == SOURCE
                assert source_peer(feed).channel_id == 123
                assert db.query(ChannelDraft).count() == 0
            service = message(101)
            service.action = object()
            client.get_messages.return_value = [
                service, message(102, '采购办公桌', 7, 'photo'),
                message(103, '', 7, 'video'), message(104, '采购，公群招募'),
                message(105, '今日天气晴朗'), message(106, '', kind='photo'),
            ]
            asyncio.run(collect_feed(engine, fid))
            with session_scope() as db:
                rows = db.query(ChannelDraft).order_by(ChannelDraft.id).all()
                assert [r.status for r in rows] == ['draft', 'filtered', 'filtered', 'filtered']
                assert rows[0].body_html == '采购办公桌'
                assert json.loads(rows[0].message_ids) == [102, 103]
                assert [m['kind'] for m in json.loads(rows[0].media)] == ['photo', 'video']
                assert not rows[0].automatic and rows[0].publish_target is None
                assert rows[1].reason == '命中排除词：公群'
                assert rows[2].reason == rows[3].reason == '未命中收录关键词'
                assert db.get(ChannelFeed, fid).cursor == 106
        finally:
            manager.running = False
            manager.workers.clear()
    client.send_message.assert_not_called()
    client.send_file.assert_not_called()


@pytest.mark.parametrize('entity', [
    NS(broadcast=False, megagroup=False),
    NS(broadcast=False, megagroup=True, access_hash=None),
])
def test_unsupported_or_incomplete_source_stays_paused(entity):
    fid = fixture_feed(False)
    with TestClient(app) as c:
        login(c)
        _, client = fake_engine()
        client.get_entity.return_value = entity
        manager.running = True
        manager.workers[1] = NS(client=client)
        try:
            assert c.post(f'/api/channel-feeds/{fid}/start', headers=HEAD).status_code == 422
            client.get_messages.assert_not_called()
            with session_scope() as db:
                feed = db.get(ChannelFeed, fid)
                assert not feed.enabled and feed.cursor == 10
        finally:
            manager.running = False
            manager.workers.clear()


def test_group_source_does_not_allow_group_destination():
    fid = fixture_feed(False)
    with session_scope() as db:
        db.get(ChannelFeed, fid).auto_publish = True
    with TestClient(app) as c:
        login(c)
        _, client = fake_engine()
        client.get_entity.side_effect = [
            Channel(id=123, title='来源群', photo=None, date=datetime.now(timezone.utc),
                    access_hash=12, megagroup=True),
            NS(broadcast=False, megagroup=True, creator=True),
        ]
        manager.running = True
        manager.workers[1] = NS(client=client)
        try:
            r = c.post(f'/api/channel-feeds/{fid}/start', headers=HEAD)
            assert r.status_code == 422 and r.json()['detail'] == '发布目标需要选择频道'
            with session_scope() as db:
                assert not db.get(ChannelFeed, fid).enabled
        finally:
            manager.running = False
            manager.workers.clear()


def test_include_rules_save_preview_and_older_clients():
    fid = fixture_feed(False)
    with TestClient(app) as c:
        login(c)
        payload = dict(name='供需草稿', account_id=1, source=str(SOURCE),
                       include_terms='采购,供应\nＡＩ\n采购', exclude_terms='公群',
                       ad_terms='付费推广', filter_ads=True)
        r = c.put(f'/api/channel-feeds/{fid}', json=payload, headers=HEAD)
        assert r.status_code == 200
        assert r.json()['include_terms'] == '采购\n供应\nＡＩ'
        assert not r.json()['enabled'] and not r.json()['auto_publish']
        rules = {k: payload[k] for k in ('include_terms', 'exclude_terms', 'ad_terms', 'filter_ads')}
        for text, expected in [('采购办公用品', True), ('ai 技术咨询', True),
                               ('供应广告，付费推广', False), ('公群采购', False),
                               ('随便聊聊', False), ('', False)]:
            response = c.post('/api/channel-filter/preview', json=rules | dict(text=text), headers=HEAD)
            assert response.status_code == 200
            assert response.json()['accepted'] is expected
        payload.pop('include_terms')
        r = c.put(f'/api/channel-feeds/{fid}', json=payload, headers=HEAD)
        assert r.json()['include_terms'] == '采购\n供应\nＡＩ'
        payload['include_terms'] = ''
        assert c.put(f'/api/channel-feeds/{fid}', json=payload, headers=HEAD).json()['include_terms'] == ''
        assert c.post('/api/channel-filter/preview', json=dict(text='保留旧行为', exclude_terms=''), headers=HEAD).json()['accepted']
        payload['include_terms'] = '\n'.join(str(n) for n in range(101))
        assert c.put(f'/api/channel-feeds/{fid}', json=payload, headers=HEAD).status_code == 422
        assert c.post('/api/channel-filter/preview', json=dict(text='test', include_terms=payload['include_terms']), headers=HEAD).status_code == 422


def test_include_terms_migration_is_repeatable_and_preserves_existing_state(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    with engine.begin() as c:
        c.exec_driver_sql('create table channel_feeds (id integer primary key, cursor integer, enabled boolean)')
        c.exec_driver_sql('insert into channel_feeds values (1,123,1)')
    add_channel_include_terms(engine)
    add_channel_include_terms(engine)
    with engine.begin() as c:
        assert c.exec_driver_sql('select cursor,enabled,include_terms from channel_feeds').one() == (123, 1, '')
        c.exec_driver_sql("update channel_feeds set include_terms='采购'")
    add_channel_include_terms(engine)
    with engine.connect() as c:
        assert c.exec_driver_sql('select include_terms from channel_feeds').scalar() == '采购'
