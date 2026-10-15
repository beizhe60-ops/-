"""One-shot group campaigns, separate from relay and AI settings/queues."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from telethon.errors import FloodWaitError, RPCError
from app.database import get_db, session_scope
from app.models import Account
from app.relay_models import BroadcastTask, BroadcastDelivery
from app.telegram_client import SEND_TIMEOUT_SECONDS

router = APIRouter()
log = logging.getLogger(__name__)
GroupId = Annotated[int, Field(strict=True, ge=-(2**52), le=-1)]


class BroadcastInput(BaseModel):
    request_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    name: str = Field(min_length=1, max_length=120)
    account_id: int = Field(gt=0, strict=True)
    targets: list[GroupId] = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=3500)
    scheduled_at: datetime | None = None

    @field_validator("name", "text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称和内容请勿留空")
        return value.strip()

    @field_validator("scheduled_at")
    @classmethod
    def aware_time(cls, value):
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("定时发送必须包含时区")
        return value.astimezone(timezone.utc).replace(tzinfo=None)


def task_payload(db, task):
    counts = {}
    for job in db.query(BroadcastDelivery).filter_by(task_id=task.id):
        counts[job.status] = counts.get(job.status, 0) + 1
    return dict(id=task.id, name=task.name, account_id=task.account_id,
                targets=json.loads(task.targets), text=task.text, status=task.status,
                scheduled_at=task.scheduled_at, created_at=task.created_at, counts=counts)


def required_task(db, task_id):
    task = db.get(BroadcastTask, task_id)
    if not task:
        raise HTTPException(404, "群发任务不存在")
    return task


@router.get("/api/broadcasts")
def listing(db: Session = Depends(get_db)):
    return [task_payload(db, t) for t in db.query(BroadcastTask).order_by(BroadcastTask.id.desc()).limit(100)]


@router.post("/api/broadcasts")
async def create(data: BroadcastInput, db: Session = Depends(get_db)):
    # Async handler has no awaits: check + commit is serialized in the single process.
    existing = db.query(BroadcastTask).filter_by(request_key=data.request_key).first()
    if existing:
        if (existing.name, existing.account_id, existing.text, json.loads(existing.targets), existing.scheduled_at) != (data.name, data.account_id, data.text, sorted(set(data.targets)), data.scheduled_at):
            raise HTTPException(409, "同一请求已保存，请刷新后重新创建")
        return task_payload(db, existing)
    account = db.get(Account, data.account_id)
    if not account or not account.session_string_encrypted:
        raise HTTPException(422, "请先登录发送账号")
    if data.scheduled_at and data.scheduled_at <= datetime.utcnow():
        raise HTTPException(422, "请选择未来的发送时间")
    task = BroadcastTask(**data.model_dump(exclude={"targets"}), targets=json.dumps(sorted(set(data.targets))), status="draft")
    db.add(task)
    db.commit()
    return task_payload(db, task)


@router.post("/api/broadcasts/{task_id}/{action}")
async def change(task_id: int, action: str, db: Session = Depends(get_db)):
    task = required_task(db, task_id)
    if action == "start":
        if task.status in ("queued", "completed"):
            return task_payload(db, task)
        if task.status not in ("draft", "paused"):
            raise HTTPException(409, "该任务已结束，请新建任务")
        account = db.get(Account, task.account_id)
        if not account or not account.session_string_encrypted or not account.send_enabled:
            raise HTTPException(422, "发送账号需登录并开启发送权限")
        if task.status == "draft":
            for target in json.loads(task.targets):
                db.add(BroadcastDelivery(task_id=task.id, chat_id=target, due_at=task.scheduled_at or datetime.utcnow()))
        task.status = "queued"
    elif action == "pause":
        if task.status == "queued":
            task.status = "paused"
    elif action == "cancel":
        task.status = "cancelled"
        db.query(BroadcastDelivery).filter(BroadcastDelivery.task_id == task.id, BroadcastDelivery.status.in_(["pending", "waiting"])).update({"status": "cancelled"})
    else:
        raise HTTPException(404)
    db.commit()
    return task_payload(db, task)


@router.get("/api/broadcasts/{task_id}/records")
def records(task_id: int, db: Session = Depends(get_db)):
    required_task(db, task_id)
    return [dict(chat_id=j.chat_id, status=j.status, error=j.error, sent_at=j.sent_at)
            for j in db.query(BroadcastDelivery).filter_by(task_id=task_id).order_by(BroadcastDelivery.id)]


def recover_broadcasts():
    with session_scope() as db:
        db.query(BroadcastDelivery).filter_by(status="sending").update({"status": "unknown", "error": "服务中断，结果待核实；不会自动重发"})


async def broadcast_loop(manager):
    """Independent lifecycle; shares account locks and Telegram wait state."""
    pending = {}
    try:
        while manager.running:
            for aid, work in list(pending.items()):
                if work.done():
                    if not work.cancelled() and work.exception():
                        log.error("Broadcast account operation failed: %s", type(work.exception()).__name__)
                    pending.pop(aid)
            try:
                with session_scope() as db:
                    ids = [row[0] for row in db.query(BroadcastTask.account_id).join(BroadcastDelivery, BroadcastDelivery.task_id == BroadcastTask.id).filter(
                        BroadcastTask.status == "queued", BroadcastDelivery.status.in_(["pending", "waiting"]),
                        BroadcastDelivery.due_at <= datetime.utcnow()).distinct().all()]
                    for task in db.query(BroadcastTask).filter_by(status="queued").all():
                        if not db.query(BroadcastDelivery.id).filter(BroadcastDelivery.task_id == task.id, BroadcastDelivery.status.in_(["pending", "waiting", "sending"])).first():
                            task.status = "completed"
            except Exception as exc:
                log.error("Broadcast queue check failed: %s", type(exc).__name__)
                await asyncio.sleep(5)
                continue
            for aid in ids:
                if len(pending) >= 2:
                    break
                if aid not in pending and not manager.account_lock(aid).locked():
                    pending[aid] = asyncio.create_task(deliver_next(manager, aid))
            await asyncio.sleep(5)
    finally:
        for work in pending.values():
            work.cancel()
        await asyncio.gather(*pending.values(), return_exceptions=True)


async def deliver_next(manager, account_id):
    async with manager.account_lock(account_id):
        worker = manager.workers.get(account_id)
        if not manager.running or not worker or not worker.client.is_connected():
            return
        with session_scope() as db:
            account = db.get(Account, account_id)
            if not account or not account.session_string_encrypted or not account.send_enabled or account.status not in ("active", "flood_wait"):
                return
            if account.flood_wait_until and account.flood_wait_until > datetime.utcnow():
                return
            row = db.query(BroadcastDelivery, BroadcastTask).join(BroadcastTask, BroadcastTask.id == BroadcastDelivery.task_id).filter(
                BroadcastTask.account_id == account_id, BroadcastTask.status == "queued",
                BroadcastDelivery.status.in_(["pending", "waiting"]), BroadcastDelivery.due_at <= datetime.utcnow()).order_by(BroadcastDelivery.id).first()
            if not row:
                return
            job, task = row
            job.status, job.error = "sending", None
            job_id, target, text = job.id, job.chat_id, task.text
        # No await between the final pause/permission check and send invocation.
        status, error, message_id, due = "unknown", "发送结果待核实；不会自动重发", None, None
        try:
            sent = await asyncio.wait_for(worker.client.send_message(target, text, parse_mode=None), SEND_TIMEOUT_SECONDS)
            status, error, message_id = "sent", None, sent.id
        except FloodWaitError as exc:
            status, error = "waiting", f"Telegram 要求等待 {exc.seconds} 秒"
            due = datetime.utcnow() + timedelta(seconds=exc.seconds)
        except RPCError as exc:
            status, error = "failed", "Telegram 拒绝发送：" + type(exc).__name__
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = "发送结果待核实（" + type(exc).__name__ + "）；不会自动重发"
        finally:
            with session_scope() as db:
                current = db.get(BroadcastDelivery, job_id)
                current.status, current.error, current.sent_message_id = status, error, message_id
                if status == "sent":
                    current.sent_at = datetime.utcnow()
                if due:
                    current.due_at = due
                    db.get(Account, account_id).flood_wait_until = due
                    db.get(Account, account_id).status = "flood_wait"
                    if db.get(BroadcastTask, current.task_id).status == "cancelled":
                        current.status = "cancelled"
