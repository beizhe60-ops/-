"""Single-process, persistent A → relay group → B delivery engine.
No sends happen in HTTP requests. Telegram events produce durable jobs.
"""

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError
from telethon import events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import User

from app.crypto import decrypt_text
from app.database import session_scope
from app.models import Account, UserGuard
from app.relay_models import RelayTask, RelayJob, ContactReceipt
from app.relay_models import AccountProfile
from app.relay_logic import filter_message, format_relay, parse_relay, render_copy
from app.telegram_client import make_client

log = logging.getLogger(__name__)


class RelayEngine:
    def __init__(self):
        self.running = False
        self.workers = {}
        self.identities = {}
        self.loop_task = None
        self.lock = asyncio.Lock()
        self.account_locks = {}

    async def start(self):
        if self.running:
            return
        # A crash after sending may precede the local commit: never retry blindly.
        with session_scope() as db:
            db.query(RelayJob).filter(RelayJob.status == "sending").update(
                {"status": "unknown", "error": "服务中断，发送结果不确定；不会自动重发"}
            )
        self.running = True
        self.loop_task = asyncio.create_task(self.run())

    async def stop(self):
        self.running = False
        if self.loop_task:
            self.loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.loop_task
        for w in list(self.workers.values()):
            with contextlib.suppress(Exception):
                await w.client.disconnect()
        self.workers.clear()
        self.identities.clear()

    async def disconnect_account(self, account_id):
        worker = self.workers.pop(account_id, None)
        self.identities.pop(account_id, None)
        if worker:
            await worker.client.disconnect()

    async def reload_workers(self):
        async with self.lock:
            await self._reload_workers()

    async def _reload_workers(self):
        if not self.running:
            return
        with session_scope() as db:
            rows = (
                db.query(Account)
                .filter(
                    Account.status.in_(["active", "flood_wait", "connection_error"])
                )
                .all()
            )
        wanted = {a.id for a in rows if a.session_string_encrypted}
        for account_id in list(self.workers):
            if account_id not in wanted:
                await self.disconnect_account(account_id)
        for account in rows:
            if account.id not in wanted:
                continue
            existing = self.workers.get(account.id)
            if existing and existing.client.is_connected():
                continue
            if existing:
                await self.disconnect_account(account.id)
            if (
                account.flood_wait_until
                and account.flood_wait_until > datetime.utcnow()
            ):
                continue
            client = make_client(
                account, decrypt_text(account.session_string_encrypted)
            )
            try:
                await asyncio.wait_for(client.connect(), timeout=20)
                if not await client.is_user_authorized():
                    raise ValueError("登录已失效，请重新登录")
                me = await client.get_me()
                # Fill this session's entity cache, including the relay group.
                await client.get_dialogs()
                self.identities[account.id] = me.id

                async def receive(event, aid=account.id):
                    try:
                        await self.on_message(aid, event)
                    except Exception:
                        log.exception("Message handler failed for account %s", aid)

                client.add_event_handler(receive, events.NewMessage())
                self.workers[account.id] = SimpleNamespace(client=client)
                with session_scope() as db:
                    row = db.get(Account, account.id)
                    row.status = "active"
                    row.last_error = None
                    row.last_health_check_at = datetime.utcnow()
            except asyncio.CancelledError:
                await client.disconnect()
                raise
            except Exception as exc:
                await client.disconnect()
                with session_scope() as db:
                    row = db.get(Account, account.id)
                    row.last_error = type(exc).__name__ + ": " + str(exc)
                    row.status = (
                        "login_required"
                        if isinstance(exc, ValueError)
                        else "connection_error"
                    )

    async def run(self):
        try:
            ticks = 0
            while self.running:
                try:
                    if ticks % 10 == 0:
                        await self.reload_workers()
                    await self.process_pending_queue()
                    ticks += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Relay service iteration failed")
                await asyncio.sleep(2)
        finally:
            self.running = False

    async def on_message(self, account_id, event):
        if not self.running or not (event.is_group or event.is_channel):
            return
        text = event.raw_text or ""
        if not text.strip():
            return
        with session_scope() as db:
            tasks = db.query(RelayTask).filter(RelayTask.enabled.is_(True)).all()
            profile = db.get(AccountProfile, account_id)
            monitor_ids = (
                set(json.loads(profile.monitor_chat_ids))
                if profile and profile.role == "monitor"
                else set()
            )
        for task in tasks:
            if (
                account_id == task.account_a
                and int(event.chat_id) in monitor_ids
                and int(event.chat_id) in json.loads(task.source_chats)
                and not event.out
            ):
                sender = await event.get_sender()
                if (
                    not isinstance(sender, User)
                    or sender.id in self.identities.values()
                ):
                    continue
                username = sender.username or ""
                ok, reason = filter_message(task, text, username, sender.id, sender.bot)
                if not ok:
                    continue
                chat = await event.get_chat()
                title = getattr(chat, "title", str(event.chat_id))
                try:
                    formatted = format_relay(title, text, username)
                except ValueError:
                    continue
                self.enqueue(
                    task,
                    "relay",
                    account_id,
                    event.chat_id,
                    event.id,
                    sender.id,
                    username,
                    title,
                    text,
                    formatted,
                )
            if (
                profile
                and profile.role == "sender"
                and account_id == task.account_b
                and int(event.chat_id) == task.relay_chat
            ):
                trusted_id = self.identities.get(task.account_a)
                if not trusted_id or event.sender_id != trusted_id:
                    continue
                parsed = parse_relay(text)
                if not parsed:
                    continue
                # Match the original source record when available. Group titles can
                # themselves contain hyphens; never infer an identity from body text.
                with session_scope() as db:
                    source = (
                        db.query(RelayJob)
                        .filter(
                            RelayJob.task_id == task.id,
                            RelayJob.stage == "relay",
                            RelayJob.text == text,
                            RelayJob.status.in_(["sending", "sent", "unknown"]),
                        )
                        .order_by(RelayJob.id.desc())
                        .first()
                    )
                if source:
                    parsed.title, parsed.text = (
                        source.source_title,
                        source.original_text,
                    )
                # Recheck filters/ignored users at the receiving side as well.
                ok, _ = filter_message(task, parsed.text, parsed.username)
                if not ok:
                    continue
                self.enqueue(
                    task,
                    "dm",
                    account_id,
                    event.chat_id,
                    event.id,
                    source.user_id if source else None,
                    parsed.username,
                    parsed.title,
                    parsed.text,
                    render_copy(task.template, parsed.username),
                )

    def enqueue(
        self,
        task,
        stage,
        account_id,
        chat_id,
        message_id,
        user_id,
        username,
        title,
        original,
        text,
    ):
        try:
            with session_scope() as db:
                # Check current state, not only the event's earlier snapshot.
                current = db.get(RelayTask, task.id)
                if not current or not current.enabled:
                    return
                db.add(
                    RelayJob(
                        task_id=task.id,
                        stage=stage,
                        account_id=account_id,
                        chat_id=chat_id,
                        message_id=message_id,
                        user_id=user_id,
                        username=username,
                        source_title=title,
                        original_text=original,
                        text=text,
                    )
                )
        except IntegrityError:
            pass  # Same Telegram event after reconnect: already handled.

    async def process_pending_queue(self, limit=10):
        async with self.lock:
            with session_scope() as db:
                jobs = (
                    db.query(RelayJob.id)
                    .join(RelayTask)
                    .filter(
                        RelayTask.enabled.is_(True),
                        RelayJob.status.in_(["pending", "waiting"]),
                        RelayJob.due_at <= datetime.utcnow(),
                    )
                    .order_by(RelayJob.id)
                    .limit(limit)
                    .all()
                )
            for (job_id,) in jobs:
                if not self.running:
                    return
                await self.send_job(job_id)

    async def send_job(self, job_id):
        with session_scope() as db:
            job = db.get(RelayJob, job_id)
            if not job or job.status not in ("pending", "waiting"):
                return
            task = db.get(RelayTask, job.task_id)
            account = db.get(Account, job.account_id)
            if (
                not task.enabled
                or not account
                or account.status not in ("active", "flood_wait")
                or not account.send_enabled
            ):
                return
            if job.stage == "dm" and not account.private_message_enabled:
                return
            if (
                account.flood_wait_until
                and account.flood_wait_until > datetime.utcnow()
            ):
                job.status = "waiting"
                job.due_at = account.flood_wait_until
                return
            worker = self.workers.get(account.id)
            if not worker or not worker.client.is_connected():
                return
            client = worker.client
            stage, username = job.stage, job.username
            destination = task.relay_chat
        try:
            if stage == "dm":
                entity = await client.get_entity("@" + username)
                if (
                    not isinstance(entity, User)
                    or entity.bot
                    or entity.deleted
                    or entity.id in self.identities.values()
                ):
                    raise ValueError("目标不是可联系的个人用户")
                destination = entity
                with session_scope() as db:
                    job = db.get(RelayJob, job_id)
                    task = db.get(RelayTask, job.task_id)
                    if (
                        not self.running
                        or not task.enabled
                        or job.status not in ("pending", "waiting")
                    ):
                        return
                    if job.user_id is not None and job.user_id != entity.id:
                        job.status, job.error = (
                            "skipped",
                            "用户名对应的用户已变化，停止发送",
                        )
                        return
                    job.user_id = entity.id
                    if str(entity.id) in [
                        x.strip()
                        for x in task.ignore_users.replace(",", "\n").splitlines()
                    ]:
                        job.status, job.error = "skipped", "用户在忽略名单中"
                        return
                    guard = (
                        db.query(UserGuard)
                        .filter(UserGuard.telegram_user_id == entity.id)
                        .first()
                    )
                    if guard and (guard.blacklisted or guard.unsubscribed):
                        job.status, job.error = "skipped", "用户在不再联系名单中"
                        return
                    receipt = db.get(ContactReceipt, entity.id)
                    if receipt and receipt.job_id != job_id:
                        job.status, job.error = (
                            "skipped",
                            "该用户已有联系记录，跨任务去重",
                        )
                        return
                    if not receipt:
                        db.add(ContactReceipt(user_id=entity.id, job_id=job_id))
            with session_scope() as db:
                job = db.get(RelayJob, job_id)
                task = db.get(RelayTask, job.task_id)
                account = db.get(Account, job.account_id)
                if (
                    not self.running
                    or not task.enabled
                    or not account.send_enabled
                    or account.status not in ("active", "flood_wait")
                    or job.status not in ("pending", "waiting")
                ):
                    return
                if stage == "dm" and not account.private_message_enabled:
                    return
                job.status = "sending"
                text = job.text
            # Disable implicit Markdown parsing so user text/mentions stay literal.
            sent = await client.send_message(
                destination, text, parse_mode=None, link_preview=False
            )
            with session_scope() as db:
                job = db.get(RelayJob, job_id)
                job.status, job.error = "sent", None
                job.sent_at, job.sent_message_id = datetime.utcnow(), sent.id
        except FloodWaitError as exc:
            with session_scope() as db:
                job = db.get(RelayJob, job_id)
                job.status, job.error = "waiting", f"Telegram 要求等待 {exc.seconds} 秒"
                job.due_at = datetime.utcnow() + timedelta(seconds=exc.seconds)
                account = db.get(Account, job.account_id)
                account.flood_wait_until = job.due_at
        except asyncio.CancelledError:
            with session_scope() as db:
                job = db.get(RelayJob, job_id)
                if job.status == "sending":
                    job.status, job.error = (
                        "unknown",
                        "发送过程中停止，结果不确定，不会自动重发",
                    )
            raise
        except Exception as exc:
            with session_scope() as db:
                job = db.get(RelayJob, job_id)
                ambiguous = job.status == "sending" and not isinstance(
                    exc, (RPCError, ValueError)
                )
                job.status = "unknown" if ambiguous else "failed"
                job.error = type(exc).__name__ + ": " + str(exc)


manager = RelayEngine()
