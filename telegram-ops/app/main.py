from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import get_settings
from app.auth import admin_password_ok, client_ip_allowed, create_session_token, current_admin
from app.crypto import encrypt_text
from app.database import get_db, init_db
from app.enums import ACCOUNT_STATUS_DISABLED
from app.models import Account, Chat, Lead, Rule, SendLog, SendQueue, UserGuard
from app.workers import send_login_code, sync_account_chats, verify_login_code
from app.relay_engine import manager
from app.console_api import router as console_router


settings = get_settings()
templates = Jinja2Templates(directory=str(settings.templates_dir))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_secret_key == "change-me-at-least-32-chars" or (settings.admin_password == "admin123456" and not settings.admin_password_hash):
        raise RuntimeError("请先运行 scripts/setup_console.py 生成独立后台凭据")
    init_db()
    from app.database import session_scope
    from app.relay_models import ConsoleState
    # One process owns Telegram sessions and delivery queue.
    import fcntl
    from pathlib import Path
    from app.database import engine
    lock_path = Path(engine.url.database).resolve().with_suffix(".runtime.lock") if engine.dialect.name == "sqlite" and engine.url.database else Path(".console-runtime.lock")
    process_lock = open(lock_path, "w")
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        process_lock.close()
        raise RuntimeError("Only one console server process may run per working directory")
    with session_scope() as db:
        saved = db.get(ConsoleState, "running")
        should_start = saved.value == "true" if saved else settings.auto_start_telegram_workers
    if should_start:
        await manager.start()
    try:
        yield
    finally:
        await manager.stop()
        process_lock.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
app.include_router(console_router)


PUBLIC_PATH_PREFIXES = ("/static", "/admin/login", "/favicon.ico")


@app.middleware("http")
async def require_admin_auth(request: Request, call_next):
    path = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        from urllib.parse import urlparse
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
        if path.startswith("/api/") and request.headers.get("x-console-request") != "1":
            return JSONResponse({"detail": "缺少请求校验标记"}, status_code=403)
    client_ip = request.client.host if request.client else None
    if not client_ip_allowed(client_ip):
        return JSONResponse({"detail": "ip is not allowed"}, status_code=403)
    if path in ("/healthz",) or any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES):
        return await call_next(request)
    if current_admin(request):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if not path.startswith("/api/") and ("text/html" in request.headers.get("accept", "") or request.method == "GET"):
        next_url = str(request.url.path)
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(f"/admin/login?next={next_url}", status_code=303)
    return JSONResponse({"detail": "admin authentication required"}, status_code=401)


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/console", status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, next: str = "/"):
    if current_admin(request):
        return redirect(next or "/")
    return templates.TemplateResponse("admin_login.html", {"request": request, "next": next, "error": None})


@app.post("/admin/login")
def admin_login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if username != settings.admin_username or not admin_password_ok(password):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "next": next, "error": "用户名或密码错误"},
            status_code=401,
        )
    response = redirect(next or "/")
    response.set_cookie(
        settings.admin_session_cookie,
        create_session_token(username),
        max_age=settings.admin_session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.admin_cookie_secure,
    )
    return response


@app.get("/admin/logout")
def admin_logout():
    response = redirect("/admin/login")
    response.delete_cookie(settings.admin_session_cookie)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    return redirect("/console")
    data = {
        "accounts": db.query(Account).count(),
        "active_accounts": db.query(Account).filter(Account.status == "active").count(),
        "chats": db.query(Chat).count(),
        "rules": db.query(Rule).count(),
        "leads": db.query(Lead).count(),
        "pending": db.query(SendQueue).filter(SendQueue.status == "pending").count(),
    }
    return templates.TemplateResponse("dashboard.html", {"request": request, "data": data})


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, db: Session = Depends(get_db)):
    accounts = db.query(Account).order_by(desc(Account.created_at)).all()
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": accounts})


@app.post("/accounts")
def create_account(
    name: str = Form(""),
    phone: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(...),
    send_enabled: bool = Form(False),
    private_message_enabled: bool = Form(False),
    proxy_enabled: bool = Form(False),
    proxy_type: str = Form("socks5"),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
    db: Session = Depends(get_db),
):
    account = Account(
        name=name or phone,
        phone=phone,
        api_id=api_id,
        api_hash_encrypted=encrypt_text(api_hash),
        send_enabled=send_enabled,
        private_message_enabled=private_message_enabled,
        proxy_enabled=proxy_enabled,
        proxy_type=proxy_type or None,
        proxy_host=proxy_host or None,
        proxy_port=int(proxy_port) if proxy_port.strip() else None,
        proxy_username=proxy_username or None,
        proxy_password_encrypted=encrypt_text(proxy_password) if proxy_password else None,
    )
    db.add(account)
    db.commit()
    return redirect("/accounts")


@app.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    account.status = ACCOUNT_STATUS_DISABLED if account.status != ACCOUNT_STATUS_DISABLED else "login_required"
    db.commit()
    return redirect("/accounts")


@app.post("/accounts/{account_id}/send-code")
async def account_send_code(account_id: int):
    await send_login_code(account_id)
    return redirect(f"/accounts/{account_id}/login")


@app.get("/accounts/{account_id}/login", response_class=HTMLResponse)
def login_page(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    return templates.TemplateResponse("login.html", {"request": request, "account": account})


@app.post("/accounts/{account_id}/verify")
async def account_verify(account_id: int, code: str = Form(...), password: str = Form("")):
    await verify_login_code(account_id, code, password or None)
    await manager.reload_workers()
    return redirect("/accounts")


@app.post("/accounts/{account_id}/sync-chats")
async def account_sync_chats(account_id: int):
    await sync_account_chats(account_id)
    return redirect("/chats")


@app.post("/workers/start")
async def workers_start():
    await manager.start()
    return redirect("/")


@app.post("/workers/stop")
async def workers_stop():
    await manager.stop()
    return redirect("/")


# @app.post("/accounts/{account_id}/health-check")
# async def account_health_check(account_id: int):
#     """手动触发测活：先同步群组，再检查测活群归属并发测试消息"""
#     await sync_account_chats(account_id)
#     worker = manager.workers.get(account_id)
#     if not worker or not worker.client:
#         raise HTTPException(400, "worker is not running")
#     await worker._on_connected()
#     return redirect("/accounts")


@app.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request, db: Session = Depends(get_db)):
    chats = db.query(Chat).join(Account).order_by(Chat.telegram_chat_id, Chat.account_id).all()
    return templates.TemplateResponse("chats.html", {"request": request, "chats": chats})


@app.post("/chats/{chat_id}/toggle")
def toggle_chat(chat_id: int, db: Session = Depends(get_db)):
    chat = db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "chat not found")
    chat.enabled = not chat.enabled
    db.commit()
    return redirect("/chats")


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, db: Session = Depends(get_db)):
    rules = db.query(Rule).order_by(desc(Rule.created_at)).all()
    return templates.TemplateResponse("rules.html", {"request": request, "rules": rules})


@app.post("/rules")
def create_rule(
    name: str = Form(...),
    keywords: str = Form(...),
    match_mode: str = Form("keyword"),
    reply_template: str = Form(""),
    send_mode: str = Form("record_only"),
    group_reply_enabled: bool = Form(False),
    private_message_enabled: bool = Form(False),
    cooldown_seconds: int = Form(86400),
    daily_limit: int = Form(50),
    enabled: bool = Form(False),
    db: Session = Depends(get_db),
):
    rule = Rule(
        name=name,
        keywords=keywords,
        match_mode=match_mode,
        reply_template=reply_template,
        send_mode=send_mode,
        group_reply_enabled=group_reply_enabled,
        private_message_enabled=private_message_enabled,
        cooldown_seconds=cooldown_seconds,
        daily_limit=daily_limit,
        enabled=enabled,
    )
    db.add(rule)
    db.commit()
    return redirect("/rules")


@app.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "rule not found")
    rule.enabled = not rule.enabled
    db.commit()
    return redirect("/rules")


@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request, db: Session = Depends(get_db)):
    leads = db.query(Lead).order_by(desc(Lead.created_at)).limit(200).all()
    return templates.TemplateResponse("leads.html", {"request": request, "leads": leads})


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(SendQueue).order_by(desc(SendQueue.created_at)).limit(200).all()
    return templates.TemplateResponse("queue.html", {"request": request, "jobs": jobs})


@app.get("/guards", response_class=HTMLResponse)
def guards_page(request: Request, db: Session = Depends(get_db)):
    guards = db.query(UserGuard).order_by(desc(UserGuard.updated_at)).limit(200).all()
    return templates.TemplateResponse("guards.html", {"request": request, "guards": guards})


@app.post("/guards")
def upsert_guard(
    telegram_user_id: int = Form(...),
    username: str = Form(""),
    blacklisted: bool = Form(False),
    unsubscribed: bool = Form(False),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = db.query(UserGuard).filter(UserGuard.telegram_user_id == telegram_user_id).first()
    if not guard:
        guard = UserGuard(telegram_user_id=telegram_user_id)
        db.add(guard)
    guard.username = username or None
    guard.blacklisted = blacklisted
    guard.unsubscribed = unsubscribed
    guard.note = note or None
    db.commit()
    return redirect("/guards")


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    logs = db.query(SendLog).order_by(desc(SendLog.created_at)).limit(200).all()
    return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})


@app.get("/healthz")
def healthz():
    return {"ok": True, "workers": list(manager.workers.keys())}


from fastapi.exceptions import RequestValidationError
@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    fields = [str(e['loc'][-1]) + ': ' + e['msg'] for e in exc.errors()]
    return JSONResponse({'detail': '；'.join(fields)}, status_code=422)
