"""Bounded channel polling and explicit one-shot publication using existing sessions."""
import asyncio
import html
import json
import logging
from datetime import datetime, timedelta, timezone
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import InputPeerChannel
from telethon import utils
from app.database import session_scope
from app.models import Account
from app.channel_models import ChannelFeed, ChannelDraft
from app.channel_rules import filter_post, prepare_post
from app.telegram_client import SEND_TIMEOUT_SECONDS

log = logging.getLogger(__name__)
READ_TIMEOUT = 20


def source_peer(feed):
    channel_id, _ = utils.resolve_id(feed.source_id)
    return InputPeerChannel(channel_id, int(feed.source_hash))


def available(account, sending=False):
    return bool(account and account.session_string_encrypted and account.status in ('active', 'flood_wait')
                and (not sending or account.send_enabled)
                and not (account.flood_wait_until and account.flood_wait_until > datetime.utcnow()))


def set_wait(account_id, seconds):
    with session_scope() as db:
        account = db.get(Account, account_id)
        if account:
            until = datetime.utcnow() + timedelta(seconds=seconds)
            account.flood_wait_until = max(account.flood_wait_until or until, until)
            account.status = 'flood_wait'


def media_kind(message):
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'video', None):
        return 'video'
    if getattr(message, 'document', None):
        return 'document'
    return None


def ingest(db, feed, messages):
    """The cursor and all resulting drafts commit together; never a partial page."""
    groups = {}
    for message in sorted(messages, key=lambda m: m.id):
        if message.id <= feed.cursor:
            continue
        # Service messages have no original post and only advance the cursor.
        if getattr(message, 'action', None):
            feed.cursor = message.id
            continue
        key = 'album:' + str(message.grouped_id) if getattr(message, 'grouped_id', None) else 'message:' + str(message.id)
        groups.setdefault(key, []).append(message)
    for key, rows in groups.items():
        text = '\n\n'.join(m.raw_text for m in rows if m.raw_text)
        media = [dict(id=m.id, kind=media_kind(m)) for m in rows if media_kind(m)]
        reason = filter_post(text, feed.exclude_terms, feed.filter_ads, feed.ad_terms)
        if not text.strip() and not media:
            feed.cursor = max(feed.cursor, rows[-1].id)
            continue
        existing = db.query(ChannelDraft).filter_by(feed_id=feed.id, source_id=feed.source_id, event_key=key).first()
        if not existing:
            source_date = rows[0].date.astimezone(timezone.utc).replace(tzinfo=None)
            db.add(ChannelDraft(feed_id=feed.id, source_id=feed.source_id, event_key=key,
                message_ids=json.dumps([m.id for m in rows]), media=json.dumps(media),
                original_text=text, body_html=html.escape(text), status='filtered' if reason else 'draft',
                reason=reason, source_date=source_date))
        # Reconciliation must never overwrite a person's draft edits.
        feed.cursor = max(feed.cursor, rows[-1].id)
    feed.checked_at, feed.error = datetime.utcnow(), None


async def collect_feed(manager, feed_id):
    with session_scope() as db:
        feed = db.get(ChannelFeed, feed_id)
        if not feed or not feed.enabled:
            return
        account_id, revision = feed.account_id, feed.revision
        cursor, peer = feed.cursor, source_peer(feed)
        if not available(db.get(Account, account_id)):
            return
    async with manager.account_lock(account_id):
        worker = manager.workers.get(account_id)
        if not manager.running or not worker or not worker.client.is_connected():
            return
        try:
            rows = list(await asyncio.wait_for(worker.client.get_messages(peer, min_id=cursor, reverse=True, limit=100), READ_TIMEOUT))
            rows.sort(key=lambda m: m.id)
            # An album can cross the page boundary. Fetch its remaining adjacent items.
            if rows and getattr(rows[-1], 'grouped_id', None):
                tail = rows[-1]
                extras = await asyncio.wait_for(worker.client.get_messages(peer, min_id=tail.id, reverse=True, limit=10), READ_TIMEOUT)
                for message in sorted(extras, key=lambda m: m.id):
                    if getattr(message, 'grouped_id', None) != tail.grouped_id:
                        break
                    rows.append(message)
                # Give in-flight albums time to finish before creating the draft.
                if tail.date.timestamp() > datetime.now(timezone.utc).timestamp() - 4:
                    rows = [m for m in rows if getattr(m, 'grouped_id', None) != tail.grouped_id]
            with session_scope() as db:
                feed = db.get(ChannelFeed, feed_id)
                if (not manager.running or not feed or not feed.enabled or feed.revision != revision
                        or manager.workers.get(account_id) is not worker or not available(db.get(Account, account_id))):
                    return
                ingest(db, feed, rows)
        except asyncio.CancelledError:
            raise
        except FloodWaitError as exc:
            set_wait(account_id, exc.seconds)
            with session_scope() as db:
                feed = db.get(ChannelFeed, feed_id)
                if feed: feed.error = f'Telegram 要求等待 {exc.seconds} 秒，之后继续采集'
        except Exception as exc:
            with session_scope() as db:
                feed = db.get(ChannelFeed, feed_id)
                if feed: feed.error = '采集暂未完成：' + type(exc).__name__ + '；请检查账号及频道读取权限'


def recover_channel_posts():
    with session_scope() as db:
        db.query(ChannelDraft).filter_by(status='sending').update(dict(status='unknown', reason='服务中断，发布结果待核实，请先查看目标频道'))


async def publish_next(manager, account_id):
    async with manager.account_lock(account_id):
        worker = manager.workers.get(account_id)
        if not manager.running or not worker or not worker.client.is_connected():
            return
        with session_scope() as db:
            if not available(db.get(Account, account_id), sending=True):
                return
            row = db.query(ChannelDraft, ChannelFeed).join(ChannelFeed, ChannelDraft.feed_id == ChannelFeed.id).filter(
                ChannelFeed.account_id == account_id, ChannelDraft.status.in_(['queued', 'waiting']),
                ChannelDraft.due_at <= datetime.utcnow()).order_by(ChannelDraft.id).first()
            if not row:
                return
            draft, feed = row
            draft_id, revision, version = draft.id, feed.revision, draft.version
            target = draft.publish_target
            if target != feed.target_id or target == feed.source_id:
                draft.status, draft.reason = 'draft', '发布目标已变化，请重新确认'
                return
            media = json.loads(draft.media) if draft.keep_media else []
            text, entities, _ = prepare_post(draft.body_html, bool(media))
            peer = source_peer(feed)
        # Resolve and verify before marking sending; these operations have no delivery side effects.
        try:
            target_entity = await asyncio.wait_for(worker.client.get_entity(target), READ_TIMEOUT)
            if not getattr(target_entity, 'broadcast', False):
                raise ValueError('发布目标请填写频道 ID，群组不作为曝光频道')
            rights = getattr(target_entity, 'admin_rights', None)
            if not (getattr(target_entity, 'creator', False) or getattr(rights, 'post_messages', False)):
                raise ValueError('该账号需要目标频道的发布消息权限')
            attachments = []
            if media:
                source_rows = await asyncio.wait_for(worker.client.get_messages(peer, ids=[m['id'] for m in media]), READ_TIMEOUT)
                for item in source_rows:
                    if item is None or not media_kind(item):
                        raise ValueError('原附件已被删除或不可读，请移除原附件后重新编辑')
                    attachments.append(item.media)
        except asyncio.CancelledError:
            raise
        except FloodWaitError as exc:
            set_wait(account_id, exc.seconds)
            with session_scope() as db:
                d = db.get(ChannelDraft, draft_id)
                if d and d.status in ('queued', 'waiting'):
                    d.status, d.due_at, d.reason = 'waiting', datetime.utcnow()+timedelta(seconds=exc.seconds), '等待 Telegram 恢复'
            return
        except Exception as exc:
            with session_scope() as db:
                d = db.get(ChannelDraft, draft_id)
                if d and d.status in ('queued', 'waiting'):
                    d.status, d.reason = 'failed', str(exc) if isinstance(exc, ValueError) else '发布准备失败：'+type(exc).__name__
            return
        with session_scope() as db:
            draft, feed = db.get(ChannelDraft, draft_id), db.get(ChannelFeed, row[1].id)
            if not manager.running or not draft or draft.status not in ('queued','waiting'):
                return
            if (not feed or feed.revision != revision or draft.version != version or feed.target_id != target
                    or manager.workers.get(account_id) is not worker or not available(db.get(Account,account_id),sending=True)):
                draft.status, draft.reason = 'draft', '配置或账号状态发生变化，请重新确认'
                return
            draft.status, draft.reason = 'sending', None
        status, reason, sent_ids, due = 'unknown', '发布结果待核实，请先查看目标频道；系统不会自动重发', [], None
        try:
            if attachments:
                sent = await asyncio.wait_for(worker.client.send_file(target_entity, attachments if len(attachments)>1 else attachments[0],
                    caption=[text]+['']*(len(attachments)-1) if len(attachments)>1 else text,
                    formatting_entities=[entities]+[[]]*(len(attachments)-1) if len(attachments)>1 else entities,
                    parse_mode=None), SEND_TIMEOUT_SECONDS)
            else:
                sent = await asyncio.wait_for(worker.client.send_message(target_entity, text, formatting_entities=entities, parse_mode=None, link_preview=False), SEND_TIMEOUT_SECONDS)
            sent_ids = [m.id for m in (sent if isinstance(sent, list) else [sent])]
            status, reason = 'sent', None
        except FloodWaitError as exc:
            # A multi-part album could already have partially succeeded: do not retry it blindly.
            if len(attachments) > 1:
                reason = '相册发布遇到平台等待，可能已发布部分内容，请查看目标频道'
            else:
                status, reason, due = 'waiting', '等待 Telegram 恢复', datetime.utcnow()+timedelta(seconds=exc.seconds)
            set_wait(account_id, exc.seconds)
        except RPCError as exc:
            status, reason = ('unknown' if len(attachments)>1 else 'failed'), 'Telegram 返回：' + type(exc).__name__
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = '发布结果待核实（'+type(exc).__name__+'），请先查看目标频道'
        finally:
            with session_scope() as db:
                draft = db.get(ChannelDraft, draft_id)
                draft.status, draft.reason, draft.sent_ids = status, reason, json.dumps(sent_ids)
                if status == 'sent': draft.published_at = datetime.utcnow()
                if due: draft.due_at = due


async def channel_loop(manager):
    """At most two independent account operations; cancellation owns every child."""
    tasks, last_collect = {}, {}
    try:
        while manager.running:
            for aid, task in list(tasks.items()):
                if task.done():
                    if not task.cancelled() and task.exception():
                        log.error('Channel operation failed: %s', type(task.exception()).__name__)
                    tasks.pop(aid)
            try:
                with session_scope() as db:
                    feeds = db.query(ChannelFeed).filter_by(enabled=True).all()
                    publishing = {r[0] for r in db.query(ChannelFeed.account_id).join(ChannelDraft, ChannelDraft.feed_id==ChannelFeed.id).filter(
                        ChannelDraft.status.in_(['queued','waiting']),ChannelDraft.due_at<=datetime.utcnow()).distinct()}
            except Exception as exc:
                log.error('Channel queue check failed: %s', type(exc).__name__)
                await asyncio.sleep(5)
                continue
            now = asyncio.get_running_loop().time()
            # Rotate eligible accounts instead of letting low IDs monopolize the two slots.
            ids = publishing | {f.account_id for f in feeds}
            for aid in sorted(ids, key=lambda aid: last_collect.get(('account',aid),0)):
                if len(tasks)>=2: break
                if aid in tasks or manager.account_lock(aid).locked(): continue
                candidates = sorted((f for f in feeds if f.account_id==aid and now-last_collect.get(f.id,0)>=15),key=lambda f:last_collect.get(f.id,0))
                if candidates:
                    feed = candidates[0]
                    last_collect[feed.id] = now
                    tasks[aid] = asyncio.create_task(collect_feed(manager,feed.id))
                elif aid in publishing:
                    tasks[aid] = asyncio.create_task(publish_next(manager,aid))
                if aid in tasks:
                    last_collect[('account',aid)] = now
            await asyncio.sleep(3)
    finally:
        for task in tasks.values(): task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
