"""Read-only task health projections. No Telegram calls and no configuration writes."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.relay_models import (RelayTask, RelayJob, ConsoleState, AiSummaryMessage,
                              AiSummaryRun, BroadcastTask, BroadcastDelivery)
from app.channel_models import ChannelFeed, ChannelDraft
from app.relay_engine import manager
from app.console_metrics import metrics

router = APIRouter()


def next_summary_at(config, last_key="", now=None):
    if not config.get("enabled") or config.get("trigger", "schedule") != "schedule":
        return None
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    h, m = map(int, config.get("schedule_time", "23:00").split(":"))
    planned = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # The worker checks once each 30 seconds and permits the whole scheduled minute.
    if planned + timedelta(minutes=1) <= now or last_key == now.strftime("%Y-%m-%d"):
        planned += timedelta(days=1)
    return planned


def runtime_state(enabled, account, running, ready=True, sending=True, listening=False):
    if not enabled:
        return "paused", "已暂停"
    if not running:
        return "paused", "服务已暂停"
    if not ready:
        return "warning", "待配置"
    if not account or not account.session_string_encrypted:
        return "error", "账号待登录"
    worker = manager.workers.get(account.id)
    if not worker or not worker.client.is_connected():
        return "error", "账号未连接"
    if listening:
        update = getattr(worker.client, "_updates_handle", None)
        if update and update.done():
            return "error", "监听连接异常"
    if sending and not account.send_enabled:
        return "warning", "发送权限关闭"
    if account.flood_wait_until and account.flood_wait_until > datetime.utcnow():
        return "warning", "平台等待中"
    return "active", "运行中"


def task_health(db):
    accounts = {a.id: a for a in db.query(Account).all()}
    tasks = db.query(RelayTask).filter(RelayTask.deleted_at.is_(None)).order_by(RelayTask.id.desc()).all()
    deliveries = {r.task_id: r for r in db.query(
        RelayJob.task_id,
        func.max(case(((RelayJob.status == "sent") & (RelayJob.stage == "relay"), RelayJob.sent_at))).label("last_sent"),
        func.sum(case((RelayJob.status == "failed", 1), else_=0)).label("failed"),
        func.sum(case((RelayJob.status == "unknown", 1), else_=0)).label("unknown"),
    ).group_by(RelayJob.task_id).all()}
    # Snapshot once: event handlers can update this bounded in-memory map concurrently.
    received = dict(manager.recent_receipts)
    rows = []
    for t in tasks:
        status, label = runtime_state(t.enabled, accounts.get(t.account_a), manager.running,
                                      bool(t.relay_chat and json.loads(t.source_chats)), listening=True)
        receipt = max((received.get((t.account_a, chat), "") for chat in json.loads(t.source_chats)), default="") or None
        result = deliveries.get(t.id)
        rows.append(dict(kind="relay", id=t.id, name=t.name, status=status, label=label,
                         last_received=receipt, last_success=result.last_sent if result else None,
                         failed=result.failed if result else 0, unknown=result.unknown if result else 0,
                         href=f"/console/records?task_id={t.id}", action="查看记录"))

    collected = {r.feed_id: r for r in db.query(ChannelDraft.feed_id,
        func.max(ChannelDraft.created_at).label("received"),
        func.max(case((ChannelDraft.status == "sent", ChannelDraft.published_at))).label("sent"),
        func.sum(case((ChannelDraft.status.in_(["failed", "unknown"]), 1), else_=0)).label("exceptions"),
    ).group_by(ChannelDraft.feed_id).all()}
    for f in db.query(ChannelFeed).order_by(ChannelFeed.id.desc()).all():
        status, label = runtime_state(f.enabled, accounts.get(f.account_id), manager.running,
                                      bool(f.source_id and (not f.auto_publish or f.target_id)), sending=f.auto_publish)
        if status == "active" and f.error:
            status, label = "warning", "最近采集异常"
        result = collected.get(f.id)
        rows.append(dict(kind="channel", id=f.id, name=f.name, status=status, label=label,
                         last_received=result.received if result else None, last_success=result.sent if result else None,
                         checked_at=f.checked_at, exceptions=result.exceptions if result else 0,
                         href="/console/channels", action="查看采编结果"))

    saved = db.get(ConsoleState, "ai_summary_settings")
    config = json.loads(saved.value) if saved else {}
    last_run = db.get(ConsoleState, "ai_summary_last_run")
    status, label = runtime_state(config.get("enabled", False), accounts.get(config.get("account_id")), manager.running,
                                  bool(config.get("api_key_encrypted") and config.get("source_chat_ids") and config.get("target_id")))
    if not config:
        status, label = "warning", "尚未配置"
    elif config.get("trigger") == "manual":
        status, label = "paused", "仅手动生成"
    last_success = db.query(func.max(AiSummaryRun.created_at)).filter(AiSummaryRun.status == "sent").scalar()
    latest = db.query(AiSummaryRun).filter(AiSummaryRun.status != "preview").order_by(AiSummaryRun.id.desc()).first()
    last_received = db.query(func.max(AiSummaryMessage.created_at)).filter(
        AiSummaryMessage.account_id == config.get("account_id", 0),
        AiSummaryMessage.chat_id.in_(config.get("source_chat_ids", []))).scalar()
    rows.append(dict(kind="ai", id=0, name="AI 群组摘要", status=status, label=label,
                     last_received=last_received, last_success=last_success,
                     latest_status=latest.status if latest else None,
                     next_at=next_summary_at(config, last_run.value if last_run else "") if manager.running else None,
                     trigger=config.get("trigger"), trigger_count=config.get("trigger_count", 50),
                     href="/console/ai", action="查看分析结果"))

    broadcast_results = {r.task_id: r for r in db.query(BroadcastDelivery.task_id,
        func.max(case((BroadcastDelivery.status == "sent", BroadcastDelivery.sent_at))).label("sent"),
        func.sum(case((BroadcastDelivery.status.in_(["failed", "unknown"]), 1), else_=0)).label("exceptions"),
    ).group_by(BroadcastDelivery.task_id).all()}
    for b in db.query(BroadcastTask).filter(BroadcastTask.status.in_(["queued", "paused"])).order_by(BroadcastTask.id.desc()).all():
        status, label = runtime_state(b.status == "queued", accounts.get(b.account_id), manager.running)
        if status == "active" and b.scheduled_at and b.scheduled_at > datetime.utcnow():
            label = "等待计划时间"
        result = broadcast_results.get(b.id)
        rows.append(dict(kind="broadcast", id=b.id, name=b.name, status=status, label=label,
                         last_received=None, last_success=result.sent if result else None,
                         exceptions=result.exceptions if result else 0, next_at=b.scheduled_at,
                         href="/console/broadcasts", action="查看群发任务"))
    return rows


@router.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    return {"checked_at": datetime.now(timezone.utc), "tasks": task_health(db), "performance": metrics.snapshot()}
