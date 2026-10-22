"""Authenticated channel curation endpoints; publication is queued, never sent here."""
import asyncio
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session
from telethon import utils
from telethon.errors import FloodWaitError
from app.database import get_db, session_scope
from app.models import Account
from app.channel_models import ChannelFeed, ChannelDraft
from app.channel_rules import DEFAULT_AD_TERMS, filter_post, prepare_post, terms
from app.channel_service import available, source_peer, set_wait, READ_TIMEOUT
from app.message_copy import normalize_image

router = APIRouter()
GroupId = Annotated[int, Field(strict=True, ge=-(2**52), le=-1)]


class FeedInput(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    account_id: int = Field(strict=True,gt=0)
    source: str = Field(default='qt00',min_length=1,max_length=100)
    target_id: GroupId | None = None
    exclude_terms: str = Field(default='七天',max_length=4000)
    filter_ads: bool = Field(default=True,strict=True)
    ad_terms: str = Field(default=DEFAULT_AD_TERMS,max_length=4000)

    @field_validator('source')
    @classmethod
    def source_name(cls, value):
        value = value.strip()
        match = re.fullmatch(r'(?:https://t\.me/|@)?([A-Za-z][A-Za-z0-9_]{3,31})/?',value)
        if match: return match.group(1).lower()
        if re.fullmatch(r'-[1-9]\d{0,15}',value) and -(2**52)<=int(value)<0: return value
        raise ValueError('请填写公开频道链接、@用户名或完整负数频道 ID')

    @field_validator('name')
    @classmethod
    def name_nonblank(cls,value):
        if not value.strip(): raise ValueError('请填写采集名称')
        return value.strip()

    @field_validator('exclude_terms','ad_terms')
    @classmethod
    def clean_terms(cls,value):
        items = terms(value)
        if len(items)>100 or any(len(t)>100 for t in items): raise ValueError('最多 100 个关键词，每个最多 100 字')
        return '\n'.join(items)


class VersionInput(BaseModel):
    version: int = Field(strict=True,ge=1)


class DraftInput(VersionInput):
    body_html: str = Field(max_length=24000)
    keep_media: bool = Field(default=True,strict=True)


class FilterInput(BaseModel):
    text: str = Field(max_length=16000)
    exclude_terms: str = Field(default='七天',max_length=4000)
    ad_terms: str = Field(default=DEFAULT_AD_TERMS,max_length=4000)
    filter_ads: bool = Field(default=True,strict=True)


def required(db, model, key):
    value = db.get(model,key)
    if not value: raise HTTPException(404,'采集配置或草稿不存在')
    return value


def feed_payload(db,feed):
    counts = dict(db.query(ChannelDraft.status,func.count(ChannelDraft.id)).filter_by(feed_id=feed.id).group_by(ChannelDraft.status).all())
    return {k:getattr(feed,k) for k in ('id','name','account_id','source','source_id','source_title','target_id','exclude_terms','ad_terms','filter_ads','enabled','revision','started_at','checked_at','error')} | dict(counts=counts)


def draft_payload(draft, detail=False):
    media = json.loads(draft.media)
    value = {k:getattr(draft,k) for k in ('id','feed_id','source_id','status','reason','version','source_date','created_at','keep_media','publish_target','published_at')}
    value.update(message_ids=json.loads(draft.message_ids), media=media, sent_ids=json.loads(draft.sent_ids))
    value['excerpt'] = draft.original_text[:160]
    if detail: value.update(original_text=draft.original_text,body_html=draft.body_html)
    return value


@router.get('/api/channel-feeds')
def feeds(db:Session=Depends(get_db)):
    return [feed_payload(db,f) for f in db.query(ChannelFeed).order_by(ChannelFeed.id.desc()).all()]


@router.post('/api/channel-feeds')
@router.put('/api/channel-feeds/{feed_id}')
async def save_feed(data:FeedInput, feed_id:int|None=None, db:Session=Depends(get_db)):
    account = required(db,Account,data.account_id)
    if not account.session_string_encrypted: raise HTTPException(422,'请先登录采集账号')
    feed = required(db,ChannelFeed,feed_id) if feed_id else ChannelFeed()
    if feed_id and (feed.source!=data.source or feed.account_id!=data.account_id):
        if db.query(ChannelDraft.id).filter_by(feed_id=feed_id).first():
            raise HTTPException(409,'已有草稿的采集配置保留原账号和来源；更换来源请新建采集')
        feed.source_id,feed.source_hash,feed.source_title = None,None,''
    if data.target_id and feed.source_id==data.target_id:
        raise HTTPException(422,'来源频道和发布频道需要分别设置')
    for key,value in data.model_dump().items(): setattr(feed,key,value)
    feed.enabled,feed.error = False,None
    if feed_id:
        feed.revision+=1
        db.query(ChannelDraft).filter(ChannelDraft.feed_id==feed.id,ChannelDraft.status.in_(['queued','waiting'])).update(dict(status='draft',reason='配置已更新，请重新确认发布'))
    else: db.add(feed)
    db.commit()
    return feed_payload(db,feed)


@router.post('/api/channel-feeds/{feed_id}/{action}')
async def feed_action(feed_id:int,action:str):
    from app.relay_engine import manager
    with session_scope() as db:
        feed=required(db,ChannelFeed,feed_id)
        if action=='pause':
            feed.enabled=False;feed.revision+=1
            return dict(ok=True)
        if action!='start': raise HTTPException(404)
        if feed.enabled: return dict(ok=True)
        account_id,revision,source,target=feed.account_id,feed.revision,feed.source,feed.target_id
    async with manager.account_lock(account_id):
        worker=manager.workers.get(account_id)
        with session_scope() as db:
            if not manager.running or not worker or not worker.client.is_connected() or not available(db.get(Account,account_id)):
                raise HTTPException(422,'请启动后台服务，并等待采集账号连接成功')
        try:
            entity=await asyncio.wait_for(worker.client.get_entity(int(source) if source.startswith('-') else source),READ_TIMEOUT)
            if not getattr(entity,'broadcast',False): raise HTTPException(422,'来源需要选择频道，群组请使用监测转发')
            source_id=utils.get_peer_id(entity)
            if source_id==target: raise HTTPException(422,'来源频道和发布频道需要分别设置')
            latest=await asyncio.wait_for(worker.client.get_messages(entity,limit=1),READ_TIMEOUT)
        except HTTPException: raise
        except FloodWaitError as exc:
            set_wait(account_id,exc.seconds)
            raise HTTPException(422,f'Telegram 要求等待 {exc.seconds} 秒后再试')
        except Exception as exc:
            raise HTTPException(422,'频道读取验证未完成：'+type(exc).__name__+'；请检查账号是否有读取权限')
        with session_scope() as db:
            feed=required(db,ChannelFeed,feed_id)
            if feed.revision!=revision or not manager.running or manager.workers.get(account_id) is not worker or not available(db.get(Account,account_id)):
                raise HTTPException(409,'配置或连接已变化，请重试')
            if feed.source_id and feed.source_id!=source_id and db.query(ChannelDraft.id).filter_by(feed_id=feed_id).first():
                raise HTTPException(409,'频道标识已变化，请新建采集')
            feed.source_id,feed.source_hash,feed.source_title=source_id,str(entity.access_hash),entity.title[:255]
            feed.cursor=latest[0].id if latest else 0
            feed.enabled,feed.started_at,feed.checked_at,feed.error=True,datetime.utcnow(),datetime.utcnow(),None
            feed.revision+=1
    return dict(ok=True)


@router.post('/api/channel-filter/preview')
def filter_preview(data:FilterInput):
    reason=filter_post(data.text,data.exclude_terms,data.filter_ads,data.ad_terms)
    return dict(accepted=reason is None,reason=reason or '进入待编辑草稿')


@router.get('/api/channel-drafts')
def drafts(status:Literal['draft','filtered','discarded','queued','sent','failed','unknown','all']='draft',feed_id:int|None=None,offset:int=Query(0,ge=0),db:Session=Depends(get_db)):
    query=db.query(ChannelDraft)
    if feed_id: query=query.filter_by(feed_id=feed_id)
    if status=='queued':query=query.filter(ChannelDraft.status.in_(['queued','waiting','sending']))
    elif status!='all':query=query.filter_by(status=status)
    return dict(total=query.count(),items=[draft_payload(d) for d in query.order_by(ChannelDraft.id.desc()).offset(offset).limit(30)])


@router.get('/api/channel-drafts/{draft_id}')
def draft_detail(draft_id:int,db:Session=Depends(get_db)):
    return draft_payload(required(db,ChannelDraft,draft_id),True)


@router.put('/api/channel-drafts/{draft_id}')
async def edit_draft(draft_id:int,data:DraftInput,db:Session=Depends(get_db)):
    draft=required(db,ChannelDraft,draft_id)
    if draft.version!=data.version: raise HTTPException(409,'草稿已变化，请重新打开后编辑')
    if draft.status not in ('draft','failed'): raise HTTPException(409,'当前状态仅支持查看，已排队的草稿请先取消发布')
    try: _,_,clean=prepare_post(data.body_html,data.keep_media and bool(json.loads(draft.media)))
    except ValueError as exc:raise HTTPException(422,str(exc))
    draft.body_html,draft.keep_media,draft.status,draft.reason=clean,data.keep_media,'draft',None
    draft.version+=1
    db.commit()
    return draft_payload(draft,True)


@router.post('/api/channel-drafts/{draft_id}/{action}')
async def draft_action(draft_id:int,action:str,data:VersionInput,db:Session=Depends(get_db)):
    draft=required(db,ChannelDraft,draft_id)
    if draft.version!=data.version: raise HTTPException(409,'草稿已变化，请刷新后再操作')
    if action=='publish':
        if draft.status in ('queued','waiting','sending','sent'): return draft_payload(draft,True)
        if draft.status!='draft':raise HTTPException(409,'请先编辑并保存草稿')
        feed=required(db,ChannelFeed,draft.feed_id)
        if not feed.target_id:raise HTTPException(422,'请先在采集设置中填写你的发布频道 ID')
        if feed.target_id==draft.source_id:raise HTTPException(422,'来源频道和发布频道需要分别设置')
        if draft.source_id!=feed.source_id:raise HTTPException(409,'来源频道已变化，请新建草稿')
        if not available(db.get(Account,feed.account_id),sending=True):raise HTTPException(422,'请检查发布账号是否登录、开启发送及是否处于平台等待')
        try:prepare_post(draft.body_html,draft.keep_media and bool(json.loads(draft.media)))
        except ValueError as exc:raise HTTPException(422,str(exc))
        draft.status,draft.publish_target,draft.due_at,draft.reason='queued',feed.target_id,datetime.utcnow(),None
    elif action=='cancel':
        if draft.status not in ('queued','waiting'): raise HTTPException(409,'仅可取消尚未开始发送的草稿')
        draft.status,draft.reason='draft',None
    elif action=='discard':
        if draft.status not in ('draft','filtered','failed'):raise HTTPException(409,'当前状态不支持忽略')
        draft.status='discarded'
    else:raise HTTPException(404)
    draft.version+=1
    db.commit()
    return draft_payload(draft,True)


@router.get('/api/channel-drafts/{draft_id}/thumbnail/{message_id}')
async def thumbnail(draft_id:int,message_id:int):
    from app.relay_engine import manager
    with session_scope() as db:
        draft=required(db,ChannelDraft,draft_id)
        if message_id not in [m['id'] for m in json.loads(draft.media)]:raise HTTPException(404)
        feed=required(db,ChannelFeed,draft.feed_id)
        if feed.source_id!=draft.source_id:raise HTTPException(409,'来源频道配置已变化')
        account_id,peer=feed.account_id,source_peer(feed)
        if not available(db.get(Account,account_id)): raise HTTPException(422,'采集账号暂未就绪')
    async with manager.account_lock(account_id):
        worker=manager.workers.get(account_id)
        if not worker or not worker.client.is_connected():raise HTTPException(422,'请等待采集账号连接')
        try:
            message=await asyncio.wait_for(worker.client.get_messages(peer,ids=message_id),READ_TIMEOUT)
            if not message:raise HTTPException(404,'原附件已删除')
            # Only a small thumbnail is requested; original videos/documents stay on Telegram.
            data=await asyncio.wait_for(worker.client.download_media(message,file=bytes,thumb=0),READ_TIMEOUT)
            if not data or len(data)>1024*1024:raise HTTPException(404,'暂无缩略图，请查看原帖')
            image,_,_=normalize_image(data)
        except HTTPException:raise
        except FloodWaitError as exc:
            set_wait(account_id,exc.seconds)
            raise HTTPException(429,'请等待账号恢复后查看缩略图')
        except Exception:raise HTTPException(404,'缩略图暂不可读，请查看原帖')
    return Response(image,media_type='image/jpeg',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
