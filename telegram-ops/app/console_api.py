import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Account, Chat, UserGuard
from app.crypto import encrypt_text, decrypt_text
from app.relay_models import RelayTask, RelayJob, ConsoleState
from app.relay_logic import filter_message, format_relay, render_copy
from app.relay_engine import manager
from app.workers import send_login_code, verify_login_code, sync_account_chats
from app.telegram_client import make_client

router = APIRouter()


def required(db, cls, id):
    item = db.get(cls, id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return item


def account_json(a):
    connected = a.id in manager.workers and manager.workers[a.id].client.is_connected()
    return dict(
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
        "source_chats": json.loads(t.source_chats),
    }


class AccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(pattern=r"^\+[1-9][0-9]{6,14}$")
    api_id: int = Field(gt=0)
    api_hash: str = Field(pattern=r"^[a-fA-F0-9]{32}$")
    private_message_enabled: bool = False
    proxy_host: str = Field(default="", max_length=255)
    proxy_port: int | None = Field(default=None, ge=1, le=65535)
    proxy_username: str = ""
    proxy_password: str = ""


class VerifyInput(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    password: str = Field(default="", max_length=256)


class PermissionsInput(BaseModel):
    send_enabled: bool
    private_message_enabled: bool


class TaskInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_a: int
    account_b: int
    source_chats: list[int] = Field(min_length=1, max_length=200)
    relay_chat: int = Field(lt=0)
    keywords: str = Field(min_length=1, max_length=4000)
    exclude_keywords: str = Field(default="", max_length=4000)
    ignore_users: str = Field(default="", max_length=4000)
    match_mode: str = Field(default="any", pattern="^(any|all|exact)$")
    template: str = Field(min_length=1, max_length=3500)

    @field_validator("keywords", "template", "name")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("不能为空")
        return v.strip()


def validate_task(db, data):
    a, b = required(db, Account, data.account_a), required(db, Account, data.account_b)
    if a.id == b.id:
        raise HTTPException(422, "A 和 B 必须使用不同账号")
    if data.relay_chat in data.source_chats:
        raise HTTPException(422, "中转群不能同时作为来源群")
    available = {
        c.telegram_chat_id for c in db.query(Chat).filter(Chat.account_id == a.id).all()
    }
    if not set(data.source_chats).issubset(available):
        raise HTTPException(422, "来源群必须从 A 已同步的群组中选择")
    for account in (a, b):
        relay = (
            db.query(Chat)
            .filter(
                Chat.account_id == account.id, Chat.telegram_chat_id == data.relay_chat
            )
            .first()
        )
        if not relay or relay.type == "channel":
            raise HTTPException(422, "A、B 都需要加入中转群并同步群组列表")


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
    return dict(
        running=manager.running,
        accounts=[account_json(a) for a in accounts],
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
    if bool(data.proxy_host) != bool(data.proxy_port):
        raise HTTPException(422, "代理地址和端口需要同时填写")
    a = Account(
        name=data.name,
        phone=data.phone,
        api_id=data.api_id,
        api_hash_encrypted=encrypt_text(data.api_hash),
        send_enabled=True,
        private_message_enabled=data.private_message_enabled,
        proxy_enabled=bool(data.proxy_host),
        proxy_type="socks5",
        proxy_host=data.proxy_host,
        proxy_port=data.proxy_port,
        proxy_username=data.proxy_username,
        proxy_password_encrypted=encrypt_text(data.proxy_password),
    )
    db.add(a)
    db.commit()
    return account_json(a)


@router.post("/api/accounts/{id}/permissions")
def permissions(id: int, data: PermissionsInput, db: Session = Depends(get_db)):
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
    async with manager.account_locks.setdefault(id, __import__("asyncio").Lock()):
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
        "PhoneNumberInvalidError": "手机号格式不正确",
        "FloodWaitError": "Telegram 要求等待，请稍后再试",
        "ValueError": "需要两步验证密码，或账号尚未完成登录",
    }.get(type(exc).__name__, "操作失败：" + type(exc).__name__)


@router.post("/api/accounts/{id}/verify")
async def verify(id: int, data: VerifyInput, db: Session = Depends(get_db)):
    a = required(db, Account, id)
    if not a.phone_code_hash:
        raise HTTPException(400, "请先请求验证码")
    async with manager.account_locks.setdefault(id, __import__("asyncio").Lock()):
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
    async with manager.lock:
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
    t = RelayTask(**values, enabled=False)
    db.add(t)
    db.commit()
    return task_json(t)


@router.put("/api/tasks/{id}")
async def edit_task(id: int, data: TaskInput, db: Session = Depends(get_db)):
    validate_task(db, data)
    async with manager.lock:
        t = required(db, RelayTask, id)
        for key, value in data.model_dump().items():
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
def toggle_task(id: int, db: Session = Depends(get_db)):
    t = required(db, RelayTask, id)
    if not t.enabled:
        validate_task(
            db,
            TaskInput(
                **{k: v for k, v in task_json(t).items() if k not in ("id", "enabled")}
            ),
        )
        for aid in (t.account_a, t.account_b):
            a = required(db, Account, aid)
            if (
                not a.session_string_encrypted
                or not a.send_enabled
                or a.status != "active"
            ):
                raise HTTPException(400, "请先完成 A/B 账号登录，并允许账号发送消息")
        if not db.get(Account, t.account_b).private_message_enabled:
            raise HTTPException(400, "请先在账号管理中允许 B 发送私信")
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
    template: str = Field(default="", max_length=3500)


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
    rows = query.order_by(RelayJob.id.desc()).offset(max(0, offset)).limit(50).all()
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
def cancel(id: int, db: Session = Depends(get_db)):
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
def add_guard(data: GuardInput, db: Session = Depends(get_db)):
    g = db.query(UserGuard).filter(UserGuard.telegram_user_id == data.user_id).first()
    if not g:
        g = UserGuard(telegram_user_id=data.user_id)
        db.add(g)
    g.blacklisted = True
    g.note = data.note
    db.commit()
    return {"ok": True}


@router.delete("/api/guards/{id}")
def remove_guard(id: int, db: Session = Depends(get_db)):
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
