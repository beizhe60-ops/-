import json
from typing import Annotated
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Account, Chat, UserGuard
from app.crypto import encrypt_text, decrypt_text
from app.relay_models import (
    RelayTask,
    RelayJob,
    ConsoleState,
    AccountProfile,
    SenderWeight,
    SenderBinding,
)
from app.account_settings import get_profile
from app.relay_logic import filter_message, format_relay, render_copy
from app.relay_engine import manager
from app.workers import send_login_code, verify_login_code, sync_account_chats
from app.telegram_client import make_client

router = APIRouter()

DM_TEMPLATE_MAX_LENGTH = 3500
RECORDS_PAGE_SIZE = 50


def required(db, cls, id):
    item = db.get(cls, id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return item


def account_json(a: Account, db: Session) -> dict:
    profile = get_profile(db, a)
    binding = db.get(SenderBinding, a.id)
    weight = db.get(SenderWeight, a.id)
    return account_payload(a, profile, binding, weight)


def account_payload(
    a: Account,
    profile: AccountProfile,
    binding: SenderBinding | None,
    weight: SenderWeight | None,
) -> dict:
    connected = a.id in manager.workers and manager.workers[a.id].client.is_connected()
    return dict(
        receive_chat_ids=json.loads(binding.chat_ids) if binding else [],
        dm_template=binding.template if binding else "",
        role=profile.role,
        rotation_weight=weight.weight if weight else 1,
        monitor_chat_ids=json.loads(profile.monitor_chat_ids),
        id=a.id,
        name=a.name,
        phone=a.phone,
        status=a.status,
        connected=connected,
        send_enabled=a.send_enabled,
        private_message_enabled=a.private_message_enabled,
        has_session=bool(a.session_string_encrypted),
        last_error=a.last_error,
        flood_wait_until=a.flood_wait_until,
    )


def accounts_json(accounts: list[Account], db: Session) -> list[dict]:
    if not accounts:
        return []
    profiles = {p.account_id: p for p in db.query(AccountProfile).all()}
    if any(a.id not in profiles for a in accounts):
        # Preserve get_profile's legacy migration timing and side effects.
        return [account_json(a, db) for a in accounts]
    bindings = {b.account_id: b for b in db.query(SenderBinding).all()}
    weights = {w.account_id: w for w in db.query(SenderWeight).all()}
    return [account_payload(a, profiles[a.id], bindings.get(a.id), weights.get(a.id))
            for a in accounts]


def task_json(t):
    return {
        **{
            k: getattr(t, k)
            for k in (
                "id",
                "name",
                "account_a",
                "account_b",
                "relay_chat",
                "keywords",
                "exclude_keywords",
                "ignore_users",
                "match_mode",
                "template",
                "enabled",
            )
        },
        "account_b": None if t.account_b == t.account_a else t.account_b,
        "source_chats": json.loads(t.source_chats),
    }


class AccountInput(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9][0-9]{6,14}$")
    role: str = Field(pattern="^(monitor|sender)$")


class ApplicationInput(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(default="", pattern=r"^([a-fA-F0-9]{32})?$")


def application_settings(db):
    row = db.get(ConsoleState, "telegram_application")
    return json.loads(row.value) if row else None


@router.put("/api/application")
def save_application(data: ApplicationInput, db: Session = Depends(get_db)):
    existing = application_settings(db)
    if not data.api_hash and (not existing or data.api_id != existing["api_id"]):
        raise HTTPException(422, "首次配置或更换 API ID 时必须填写对应 API Hash")
    payload = {
        "api_id": data.api_id,
        "api_hash_encrypted": encrypt_text(data.api_hash)
        if data.api_hash
        else existing["api_hash_encrypted"],
    }
    db.merge(ConsoleState(key="telegram_application", value=json.dumps(payload)))
    db.commit()
    return {"configured": True, "api_id": data.api_id}


class WeightInput(BaseModel):
    weight: int = Field(strict=True, ge=1, le=1000)


@router.put("/api/accounts/{id}/weight")
async def save_weight(id: int, data: WeightInput, db: Session = Depends(get_db)):
    account = required(db, Account, id)
    if get_profile(db, account).role != "sender":
        raise HTTPException(422, "只有私信账号可以设置轮询权重")
    async with manager.lock:
        db.merge(SenderWeight(account_id=id, weight=data.weight))
        db.commit()
    return {"rotation_weight": data.weight}


GroupID = Annotated[int, Field(strict=True, ge=-(2**52), lt=0)]


class ReceiveGroupsInput(BaseModel):
    chat_ids: list[GroupID] = Field(max_length=200)
    template: str = Field(default="", max_length=DM_TEMPLATE_MAX_LENGTH)

    @field_validator("chat_ids")
    @classmethod
    def unique_ids(cls, v):
        return sorted(set(v))


@router.put("/api/accounts/{id}/receive-groups")
async def receive_groups(
    id: int, data: ReceiveGroupsInput, db: Session = Depends(get_db)
):
    account = required(db, Account, id)
    if get_profile(db, account).role != "sender":
        raise HTTPException(422, "只有私信账号可以绑定接收群组")
    if data.chat_ids and not data.template.strip():
        raise HTTPException(422, "绑定接收群组时请填写私信文案")
    async with manager.lock:
        old = db.get(SenderBinding, id)
        template_changed = old is not None and old.template != data.template.strip()
        pending = db.query(RelayJob).filter(
            RelayJob.account_id == id,
            RelayJob.stage == "dm",
            RelayJob.status.in_(["pending", "waiting"]),
        )
        for job in pending.all():
            if template_changed or job.chat_id not in data.chat_ids:
                job.status, job.error = "cancelled", "接收群组绑定或私信文案已更改"
        db.merge(
            SenderBinding(
                account_id=id,
                chat_ids=json.dumps(data.chat_ids),
                template=data.template.strip(),
            )
        )
        db.commit()
    return {"chat_ids": data.chat_ids, "message": "接收群组与私信文案已保存"}


class MonitorGroupsInput(BaseModel):
    chat_ids: list[GroupID] = Field(max_length=200)

    @field_validator("chat_ids", mode="before")
    @classmethod
    def valid_ids(cls, values):
        if not isinstance(values, list) or any(
            type(v) is not int or v >= 0 or v < -(2**52) for v in values
        ):
            raise ValueError("群组 ID 必须是完整的负整数，例如 -1001234567890")
        return sorted(set(values))


@router.put("/api/accounts/{id}/monitor-groups")
async def monitor_groups(
    id: int, data: MonitorGroupsInput, db: Session = Depends(get_db)
):
    a = required(db, Account, id)
    profile = get_profile(db, a)
    if profile.role != "monitor":
        raise HTTPException(422, "只有监测账号可以配置监测群组")
    async with manager.lock:
        profile.monitor_chat_ids = json.dumps(data.chat_ids)
        affected = 0
        for task in db.query(RelayTask).filter(RelayTask.account_a == id).all():
            current = set(json.loads(task.source_chats))
            if not current.issubset(data.chat_ids):
                task.source_chats = json.dumps(
                    sorted(current.intersection(data.chat_ids))
                )
                task.enabled = False
                affected += 1
                db.query(RelayJob).filter(
                    RelayJob.task_id == task.id,
                    RelayJob.status.in_(["pending", "waiting"]),
                ).update({"status": "cancelled", "error": "监测群组范围已更改"})
        db.commit()
    return {
        "chat_ids": data.chat_ids,
        "paused_tasks": affected,
        "message": "监测群组已保存"
        + (f"，{affected} 条受影响任务已暂停" if affected else ""),
    }


class VerifyInput(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    password: str = Field(default="", max_length=256)


class PermissionsInput(BaseModel):
    send_enabled: bool
    private_message_enabled: bool


class TaskInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_a: int
    account_b: int | None = (
        None  # Legacy API compatibility; not required for monitoring.
    )
    source_chats: list[GroupID] = Field(min_length=1, max_length=200)
    relay_chat: GroupID | None = None
    keywords: str = Field(min_length=1, max_length=4000)
    exclude_keywords: str = Field(default="", max_length=4000)
    ignore_users: str = Field(default="", max_length=4000)
    match_mode: str = Field(default="any", pattern="^(any|all|exact)$")
    template: str = Field(default="", max_length=DM_TEMPLATE_MAX_LENGTH)

    @field_validator("keywords", "name")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("不能为空")
        return v.strip()


def validate_task(db, data):
    a = required(db, Account, data.account_a)
    if get_profile(db, a).role != "monitor":
        raise HTTPException(422, "请选择监测账号")
    if data.relay_chat in data.source_chats:
        raise HTTPException(422, "转发群不能同时作为监测来源群")
    if data.account_b is not None:
        b = required(db, Account, data.account_b)
        if a.id == b.id or get_profile(db, b).role != "sender":
            raise HTTPException(422, "B 必须为独立私信账号")
    # Direct IDs are authoritative. Dialog synchronization is informational only.


def register_monitor_scope(db, account_id, sources):
    profile = get_profile(db, required(db, Account, account_id))
    profile.monitor_chat_ids = json.dumps(
        sorted(set(json.loads(profile.monitor_chat_ids)) | set(sources))
    )


@router.get("/console")
@router.get("/console/{page}")
def console_page(page: str = "overview"):
    if page not in ("overview", "accounts", "tasks", "records", "guards", "settings"):
        raise HTTPException(404)
    return FileResponse("static/console.html")


@router.get("/api/state")
def state(db: Session = Depends(get_db)):
    accounts = db.query(Account).order_by(Account.id).all()
    tasks = db.query(RelayTask).order_by(RelayTask.id.desc()).all()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    application = application_settings(db)
    return dict(
        running=manager.running,
        accounts=accounts_json(accounts, db),
        application={
            "configured": bool(application),
            "api_id": (application or {}).get("api_id"),
        },
        tasks=[task_json(t) for t in tasks],
        chats=[
            dict(
                id=c.telegram_chat_id,
                account_id=c.account_id,
                title=c.title,
                type=c.type,
            )
            for c in db.query(Chat).all()
        ],
        stats=dict(
            hits=db.query(RelayJob)
            .filter(RelayJob.stage == "relay", RelayJob.created_at >= today)
            .count(),
            pending=db.query(RelayJob)
            .filter(RelayJob.status.in_(["pending", "waiting"]))
            .count(),
            sent=db.query(RelayJob)
            .filter(
                RelayJob.stage == "dm",
                RelayJob.status == "sent",
                RelayJob.sent_at >= today,
            )
            .count(),
            failed=db.query(RelayJob)
            .filter(RelayJob.status.in_(["failed", "unknown"]))
            .count(),
        ),
    )


@router.post("/api/accounts")
def add_account(data: AccountInput, db: Session = Depends(get_db)):
    if db.query(Account).filter(Account.phone == data.phone).first():
        raise HTTPException(409, "这个手机号已经添加")
    config = application_settings(db)
    if not config:
        raise HTTPException(400, "请先在系统设置完成 Telegram 应用配置，再登录账号")
    a = Account(
        name=("监测账号" if data.role == "monitor" else "私信账号")
        + " "
        + data.phone[-4:],
        phone=data.phone,
        api_id=config["api_id"],
        api_hash_encrypted=config["api_hash_encrypted"],
        send_enabled=True,
        private_message_enabled=data.role == "sender",
    )
    db.add(a)
    db.flush()
    db.add(AccountProfile(account_id=a.id, role=data.role, monitor_chat_ids="[]"))
    if data.role == "sender":
        db.add(SenderBinding(account_id=a.id, chat_ids="[]", template=""))
    db.commit()
    return account_json(a, db)


@router.post("/api/accounts/{id}/permissions")
async def permissions(id: int, data: PermissionsInput, db: Session = Depends(get_db)):
    a = required(db, Account, id)
    a.send_enabled, a.private_message_enabled = (
        data.send_enabled,
        data.private_message_enabled,
    )
    db.commit()
    return {"ok": True}


@router.post("/api/accounts/{id}/send-code")
async def send_code(id: int, db: Session = Depends(get_db)):
    a = required(db, Account, id)
    if a.session_string_encrypted:
        raise HTTPException(409, "账号已有登录会话，请先退出账号再重新登录")
    async with manager.account_lock(id):
        try:
            await send_login_code(id)
        except Exception as exc:
            raise HTTPException(400, login_error(exc))
    return {"ok": True, "message": "验证码已请求，请查看 Telegram 或手机短信"}


def login_error(exc):
    # Do not expose credentials or phone verification values in errors/logs.
    return {
        "PhoneCodeInvalidError": "验证码不正确，请重新输入",
        "PhoneCodeExpiredError": "验证码已过期，请重新获取",
        "PasswordHashInvalidError": "两步验证密码不正确",
        "ApiIdInvalidError": "API ID 或 API Hash 不正确",
        "ApiIdPublishedFloodError": "Telegram 限制了这组公开 API 凭据，请改用自有应用凭据",
        "PhoneNumberInvalidError": "手机号格式不正确",
        "FloodWaitError": "Telegram 要求等待，请稍后再试",
        "TwoStepPasswordRequired": "需要两步验证密码，请填写后再次登录",
        "SessionPasswordNeededError": "需要两步验证密码，请填写后再次登录",
        "PhoneMigrateError": "Telegram 数据中心切换未完成，请稍后重新请求验证码",
        "NetworkMigrateError": "Telegram 数据中心切换未完成，请稍后重新请求验证码",
        "UserMigrateError": "Telegram 数据中心切换未完成，请稍后重新登录",
        "ValueError": "登录请求未完成，请检查登录配置或稍后重试",
        "ServerError": "Telegram 服务暂时异常，请稍后重试",
        "RpcCallFailError": "Telegram 服务暂时异常，请稍后重试",
        "TimeoutError": "连接 Telegram 超时，请检查网络后重试",
    }.get(type(exc).__name__, "操作失败：" + type(exc).__name__)


@router.post("/api/accounts/{id}/verify")
async def verify(id: int, data: VerifyInput, db: Session = Depends(get_db)):
    a = required(db, Account, id)
    if not a.phone_code_hash:
        raise HTTPException(400, "请先请求验证码")
    async with manager.account_lock(id):
        try:
            await verify_login_code(id, data.code, data.password or None)
        except Exception as exc:
            raise HTTPException(400, login_error(exc))
    return {"ok": True}


@router.post("/api/accounts/{id}/sync")
async def sync(id: int, db: Session = Depends(get_db)):
    a = required(db, Account, id)
    if not a.session_string_encrypted:
        raise HTTPException(400, "请先完成账号登录")
    async with manager.account_lock(id):
        await manager.disconnect_account(id)
        try:
            count = await sync_account_chats(id)
        except Exception as exc:
            raise HTTPException(400, login_error(exc))
    return {"ok": True, "message": f"已同步 {count} 个群组或频道"}


@router.post("/api/accounts/{id}/logout")
async def logout_account(id: int, db: Session = Depends(get_db)):
    a = required(db, Account, id)
    if manager.running:
        raise HTTPException(409, "请先暂停服务，再退出 Telegram 账号")
    if a.session_string_encrypted:
        client = make_client(a, decrypt_text(a.session_string_encrypted))
        try:
            await client.connect()
            await client.log_out()
        except Exception as exc:
            raise HTTPException(400, login_error(exc))
        finally:
            await client.disconnect()
    a.session_string_encrypted = None
    a.login_temp_session_string_encrypted = None
    a.phone_code_hash = None
    a.status = "login_required"
    db.commit()
    return {"ok": True}


@router.post("/api/tasks")
async def add_task(data: TaskInput, db: Session = Depends(get_db)):
    validate_task(db, data)
    values = data.model_dump()
    values["source_chats"] = json.dumps(sorted(set(data.source_chats)))
    # Keep the old non-null storage column compatible; execution never uses it as a receiver.
    values["account_b"] = data.account_b or data.account_a
    register_monitor_scope(db, data.account_a, data.source_chats)
    t = RelayTask(**values, enabled=False)
    db.add(t)
    db.commit()
    return task_json(t)


@router.put("/api/tasks/{id}")
async def edit_task(id: int, data: TaskInput, db: Session = Depends(get_db)):
    validate_task(db, data)
    async with manager.lock:
        t = required(db, RelayTask, id)
        register_monitor_scope(db, data.account_a, data.source_chats)
        values = data.model_dump()
        values["account_b"] = data.account_b or data.account_a
        for key, value in values.items():
            setattr(
                t,
                key,
                json.dumps(sorted(set(value))) if key == "source_chats" else value,
            )
        t.enabled = False
        db.query(RelayJob).filter(
            RelayJob.task_id == id, RelayJob.status.in_(["pending", "waiting"])
        ).update({"status": "cancelled", "error": "任务配置已更改，旧待发送内容已取消"})
        db.commit()
    return task_json(t)


@router.post("/api/tasks/{id}/toggle")
async def toggle_task(id: int, db: Session = Depends(get_db)):
    t = required(db, RelayTask, id)
    if not t.enabled:
        if t.relay_chat is None:
            raise HTTPException(422, "请先填写转发目标群组 ID，再启用绑定")
        if not json.loads(t.source_chats):
            raise HTTPException(422, "请先绑定至少一个监测群组 ID")
        validate_task(
            db,
            TaskInput(
                **{k: v for k, v in task_json(t).items() if k not in ("id", "enabled")}
            ),
        )
        for aid in (t.account_a,):
            a = required(db, Account, aid)
            if (
                not a.session_string_encrypted
                or not a.send_enabled
                or a.status != "active"
            ):
                raise HTTPException(400, "请先登录监测账号，并允许转发消息")

    t.enabled = not t.enabled
    db.commit()
    return task_json(t)


class PreviewInput(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    title: str = Field(default="示例来源群", max_length=255)
    username: str = Field(default="example_user", max_length=64)
    keywords: str = Field(default="", max_length=4000)
    exclude_keywords: str = Field(default="", max_length=4000)
    ignore_users: str = Field(default="", max_length=4000)
    match_mode: str = Field(default="any", pattern="^(any|all|exact)$")
    template: str = Field(default="", max_length=DM_TEMPLATE_MAX_LENGTH)


@router.post("/api/preview")
def preview(data: PreviewInput):
    t = RelayTask(
        keywords=data.keywords,
        exclude_keywords=data.exclude_keywords,
        ignore_users=data.ignore_users,
        match_mode=data.match_mode,
    )
    username = data.username.lstrip("@")
    ok, reason = filter_message(t, data.text, username)
    try:
        relay = format_relay(data.title, data.text, username) if ok else ""
    except ValueError as exc:
        ok, reason, relay = False, str(exc), ""
    return dict(
        matched=ok,
        reason=reason,
        relay=relay,
        dm=render_copy(data.template, username) if ok else "",
        simulated=True,
    )


@router.get("/api/records")
def records(
    status: str = "", q: str = "", offset: int = 0, db: Session = Depends(get_db)
):
    query = db.query(RelayJob)
    if status:
        query = query.filter(RelayJob.status == status)
    if q:
        query = query.filter(RelayJob.username.contains(q.lstrip("@"), autoescape=True))
    total = query.count()
    rows = query.order_by(RelayJob.id.desc()).offset(max(0, offset)).limit(RECORDS_PAGE_SIZE).all()
    return {
        "total": total,
        "items": [
            {
                k: getattr(j, k)
                for k in (
                    "id",
                    "task_id",
                    "stage",
                    "account_id",
                    "username",
                    "user_id",
                    "source_title",
                    "chat_id",
                    "original_text",
                    "text",
                    "status",
                    "error",
                    "created_at",
                    "sent_at",
                    "due_at",
                )
            }
            for j in rows
        ],
    }


@router.post("/api/records/{id}/cancel")
async def cancel(id: int, db: Session = Depends(get_db)):
    j = required(db, RelayJob, id)
    if j.status not in ("pending", "waiting"):
        raise HTTPException(409, "只能取消尚未发送的任务")
    j.status = "cancelled"
    db.commit()
    return {"ok": True}


@router.post("/api/service/{action}")
async def service(action: str, db: Session = Depends(get_db)):
    if action not in ("start", "stop"):
        raise HTTPException(404)
    if action == "start":
        await manager.start()
    else:
        await manager.stop()
    row = db.get(ConsoleState, "running")
    if not row:
        row = ConsoleState(key="running", value="false")
        db.add(row)
    row.value = "true" if action == "start" else "false"
    db.commit()
    return {"ok": True, "running": manager.running}


class GuardInput(BaseModel):
    user_id: int = Field(gt=0)
    note: str = Field(default="", max_length=500)


@router.get("/api/guards")
def guards(db: Session = Depends(get_db)):
    return [
        dict(id=g.id, user_id=g.telegram_user_id, note=g.note, username=g.username)
        for g in db.query(UserGuard).filter(UserGuard.blacklisted.is_(True)).all()
    ]


@router.post("/api/guards")
async def add_guard(data: GuardInput, db: Session = Depends(get_db)):
    g = db.query(UserGuard).filter(UserGuard.telegram_user_id == data.user_id).first()
    if not g:
        g = UserGuard(telegram_user_id=data.user_id)
        db.add(g)
    g.blacklisted = True
    g.note = data.note
    db.commit()
    return {"ok": True}


@router.delete("/api/guards/{id}")
async def remove_guard(id: int, db: Session = Depends(get_db)):
    g = required(db, UserGuard, id)
    db.delete(g)
    db.commit()
    return {"ok": True}


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/api/password")
def change_password(data: PasswordInput, db: Session = Depends(get_db)):
    from app.auth import admin_password_ok, hash_password

    if not admin_password_ok(data.current_password):
        raise HTTPException(400, "当前密码不正确")
    row = db.get(ConsoleState, "admin_password_hash")
    if not row:
        row = ConsoleState(key="admin_password_hash", value="")
        db.add(row)
    row.value = hash_password(data.new_password)
    db.commit()
    return {"ok": True}
