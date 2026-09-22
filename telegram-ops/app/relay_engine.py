"""Single-process, persistent A → relay group → B delivery engine.
No sends happen in HTTP requests. Telegram events produce durable jobs.
"""

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from telethon import events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import User

from app.crypto import decrypt_text
from app.database import session_scope
from app.models import Account, UserGuard
from app.relay_models import RelayTask, RelayJob, ContactReceipt
from app.relay_models import AccountProfile, SenderWeight, ConsoleState, SenderBinding
from app.relay_logic import filter_message, format_relay, parse_relay, render_copy
from app.telegram_client import (
    make_client, CONNECT_TIMEOUT_SECONDS, SEND_TIMEOUT_SECONDS, DISCONNECT_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)

MAX_ACCOUNT_OPERATIONS = 4


class RelayEngine:
    def __init__(self):
        self.running = False
        self.workers = {}
        self.identities = {}
        self.loop_task = None
        self.lock = asyncio.Lock()
        self.account_locks = {}
        self.send_tasks = {}
        self.connection_tasks = {}
        self.lifecycle_lock = asyncio.Lock()
        self.last_send_account = 0
        self.last_connection_account = 0

    def account_lock(self, account_id):
        return self.account_locks.setdefault(account_id, asyncio.Lock())

    async def start(self):
        async with self.lifecycle_lock:
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
        async with self.lifecycle_lock:
            self.running = False
            tasks = list(self.send_tasks.values()) + list(self.connection_tasks.values())
            if self.loop_task:
                tasks.append(self.loop_task)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.send_tasks.clear()
            self.connection_tasks.clear()
            for account_id in list(self.workers):
                async with self.account_lock(account_id):
                    with contextlib.suppress(Exception):
                        await self.disconnect_account(account_id)
            self.workers.clear()
            self.identities.clear()
            self.loop_task = None

    @staticmethod
    async def close_client(client):
        try:
            await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT_SECONDS)
        except TimeoutError:
            log.warning("Telegram client disconnect timed out")

    async def disconnect_account(self, account_id):
        """Caller holds this account's lock; never hold the global lock over I/O."""
        worker = self.workers.pop(account_id, None)
        self.identities.pop(account_id, None)
        if worker:
            await self.close_client(worker.client)

    def track(self, tasks, account_id, coroutine):
        task = asyncio.create_task(coroutine)
        tasks[account_id] = task

        def completed(done):
            if tasks.get(account_id) is done:
                tasks.pop(account_id, None)
            if not done.cancelled() and done.exception():
                error = done.exception()
                log.error("Account operation failed for account %s", account_id,
                          exc_info=(type(error), error, error.__traceback__))

        task.add_done_callback(completed)
        return task

    @staticmethod
    def rotate_accounts(ids, last):
        return sorted(ids, key=lambda aid: (aid <= last, aid))

    async def reload_workers(self, wait=True):
        if not self.running:
            return
        with session_scope() as db:
            accounts = db.query(Account).filter(
                Account.status.in_(["active", "flood_wait", "connection_error"])
            ).all()
        wanted = {a.id for a in accounts if a.session_string_encrypted}
        ready = {a.id for a in accounts if a.id in wanted and not (
            a.flood_wait_until and a.flood_wait_until > datetime.utcnow()
        )}
        candidates = (set(self.workers) - wanted) | {
            aid for aid in ready if aid not in self.workers
            or not self.workers[aid].client.is_connected()
        }
        launched = []
        for aid in self.rotate_accounts(candidates, self.last_connection_account):
            if len(self.connection_tasks) >= MAX_ACCOUNT_OPERATIONS:
                break
            if aid in self.connection_tasks or self.account_lock(aid).locked():
                continue
            launched.append(self.track(self.connection_tasks, aid, self.reload_account(aid)))
            self.last_connection_account = aid
        if wait and launched:
            await asyncio.gather(*launched)

    async def reload_account(self, account_id):
        async with self.account_lock(account_id):
            if not self.running:
                return
            with session_scope() as db:
                account = db.get(Account, account_id)
                wanted = account and account.session_string_encrypted and account.status in (
                    "active", "flood_wait", "connection_error"
                )
            existing = self.workers.get(account_id)
            if wanted and existing and existing.client.is_connected():
                return
            if existing:
                await self.disconnect_account(account_id)
            if not wanted or (account.flood_wait_until and account.flood_wait_until > datetime.utcnow()):
                return
            client = make_client(account, decrypt_text(account.session_string_encrypted))
            try:
                # Bound the complete handshake, not just the TCP connection.
                async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                    await client.connect()
                    if not await client.is_user_authorized():
                        raise ValueError("登录已失效，请重新登录")
                    me = await client.get_me()
                    await client.get_dialogs()
                with session_scope() as db:
                    current = db.get(Account, account_id)
                    valid = self.running and current and current.status in (
                        "active", "flood_wait", "connection_error"
                    ) and current.session_string_encrypted == account.session_string_encrypted
                    if valid:
                        current.status = "active"
                        current.last_error = None
                        current.last_health_check_at = datetime.utcnow()
                if not valid:
                    await self.close_client(client)
                    return

                async def receive(event):
                    try:
                        await self.on_message(account_id, event)
                    except Exception:
                        log.exception("Message handler failed for account %s", account_id)

                client.add_event_handler(receive, events.NewMessage())
                self.identities[account_id] = me.id
                self.workers[account_id] = SimpleNamespace(client=client)
            except asyncio.CancelledError:
                await self.close_client(client)
                raise
            except Exception as exc:
                await self.close_client(client)
                with session_scope() as db:
                    row = db.get(Account, account_id)
                    # Do not undo a concurrent disable or session replacement.
                    if row and row.status in ("active", "flood_wait", "connection_error") and row.session_string_encrypted == account.session_string_encrypted:
                        row.last_error = type(exc).__name__ + ": " + str(exc)
                        row.status = "login_required" if isinstance(exc, ValueError) else "connection_error"

    async def run(self):
        try:
            ticks = 0
            while self.running:
                try:
                    if ticks % 10 == 0:
                        await self.reload_workers(wait=False)
                    await self.process_pending_queue(wait=False)
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
            binding = db.get(SenderBinding, account_id)
            receive_ids = set(json.loads(binding.chat_ids)) if binding else set()
            monitor_ids = (
                set(json.loads(profile.monitor_chat_ids))
                if profile and profile.role == "monitor"
                else set()
            )
        for task in tasks:
            if task.relay_chat is None:
                continue
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
                and int(event.chat_id) in receive_ids
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

    def select_sender(self, db, task):
        """Persist smooth weighted round-robin state atomically with each new job."""
        candidates = []
        for account, profile in (
            db.query(Account, AccountProfile)
            .join(AccountProfile, AccountProfile.account_id == Account.id)
            .order_by(Account.id)
            .all()
        ):
            worker = self.workers.get(account.id)
            binding = db.get(SenderBinding, account.id)
            in_group = binding and task.relay_chat in json.loads(binding.chat_ids)
            if (
                profile.role != "sender"
                or not in_group
                or account.status != "active"
                or not account.send_enabled
                or not account.private_message_enabled
                or not worker
                or not worker.client.is_connected()
                or (
                    account.flood_wait_until
                    and account.flood_wait_until > datetime.utcnow()
                )
            ):
                continue
            config = db.get(SenderWeight, account.id)
            candidates.append((account.id, config.weight if config else 1))
        if not candidates:
            # The receiving account retains a waiting job if every pool member is unavailable.
            return None
        key = f"sender_rotation:{task.relay_chat}"
        row = db.get(ConsoleState, key)
        old = json.loads(row.value) if row else {}
        signature = [[aid, weight] for aid, weight in candidates]
        scores = old.get("scores", {}) if old.get("members") == signature else {}
        scores = {
            str(aid): scores.get(str(aid), 0) + weight for aid, weight in candidates
        }
        selected = max(candidates, key=lambda item: scores[str(item[0])])[0]
        scores[str(selected)] -= sum(weight for _, weight in candidates)
        db.merge(
            ConsoleState(
                key=key, value=json.dumps({"members": signature, "scores": scores})
            )
        )
        return selected

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
        if not self.running:
            return
        try:
            with session_scope() as db:
                # Check current state, not only the event's earlier snapshot.
                current = db.get(RelayTask, task.id)
                if not current or not current.enabled or current.relay_chat is None:
                    return
                # A callback may have awaited Telegram while the route was edited.
                config_fields = (
                    "account_a",
                    "source_chats",
                    "relay_chat",
                    "keywords",
                    "exclude_keywords",
                    "ignore_users",
                    "match_mode",
                    "template",
                )
                if any(getattr(current, k) != getattr(task, k) for k in config_fields):
                    return
                if stage == "relay":
                    scope = db.get(AccountProfile, current.account_a)
                    if (
                        account_id != current.account_a
                        or chat_id not in json.loads(current.source_chats)
                        or not scope
                        or scope.role != "monitor"
                        or chat_id not in json.loads(scope.monitor_chat_ids)
                    ):
                        return
                if stage == "dm":
                    if chat_id != current.relay_chat or not self.receives(
                        db, account_id, chat_id
                    ):
                        return
                    account_id = self.select_sender(db, current) or account_id
                    binding = db.get(SenderBinding, account_id)
                    template = current.template.strip() or binding.template
                    if not template.strip():
                        return
                    text = render_copy(template, username)
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

    @staticmethod
    def receives(db, account_id, chat_id):
        profile = db.get(AccountProfile, account_id)
        binding = db.get(SenderBinding, account_id)
        return bool(
            profile
            and profile.role == "sender"
            and binding
            and chat_id in json.loads(binding.chat_ids)
        )

    def job_binding_valid(self, db, job, task):
        if task.relay_chat is None:
            return False
        if job.stage == "dm":
            return job.chat_id == task.relay_chat and self.receives(
                db, job.account_id, job.chat_id
            )
        scope = db.get(AccountProfile, job.account_id)
        return bool(
            scope
            and scope.role == "monitor"
            and job.account_id == task.account_a
            and job.chat_id in json.loads(scope.monitor_chat_ids)
            and job.chat_id in json.loads(task.source_chats)
        )

    async def process_pending_queue(self, limit=10, wait=True):
        if not self.running or limit <= 0:
            return
        available = [aid for aid, w in self.workers.items()
                     if w.client.is_connected() and aid not in self.send_tasks
                     and not self.account_lock(aid).locked()]
        if not available:
            return
        with session_scope() as db:
            candidates = [row[0] for row in db.query(RelayJob.account_id)
                .join(RelayTask).join(Account, Account.id == RelayJob.account_id)
                .filter(RelayTask.enabled.is_(True),
                        RelayJob.account_id.in_(available),
                        RelayJob.status.in_(["pending", "waiting"]),
                        RelayJob.due_at <= datetime.utcnow(),
                        Account.status.in_(["active", "flood_wait"]),
                        Account.send_enabled.is_(True),
                        or_(RelayJob.stage != "dm", Account.private_message_enabled.is_(True)))
                .group_by(RelayJob.account_id).all()]
        launched = []
        for aid in self.rotate_accounts(candidates, self.last_send_account):
            if len(self.send_tasks) >= MAX_ACCOUNT_OPERATIONS:
                break
            launched.append(self.track(self.send_tasks, aid, self.process_account_queue(aid, limit)))
            self.last_send_account = aid
        if wait and launched:
            await asyncio.gather(*launched)

    async def process_account_queue(self, account_id, limit):
        async with self.account_lock(account_id):
            with session_scope() as db:
                jobs = db.query(RelayJob.id).join(RelayTask).filter(
                    RelayTask.enabled.is_(True), RelayJob.account_id == account_id,
                    RelayJob.status.in_(["pending", "waiting"]),
                    RelayJob.due_at <= datetime.utcnow(),
                ).order_by(RelayJob.id).limit(limit).all()
            for (job_id,) in jobs:
                if not self.running:
                    return
                await self._send_job(job_id)
                with session_scope() as db:
                    completed = db.get(RelayJob, job_id)
                    if completed and completed.status in ("failed", "unknown"):
                        # Yield this slot instead of consuming an entire batch on failures.
                        # Remaining jobs keep their account and resume on a later tick.
                        return

    async def send_job(self, job_id):
        with session_scope() as db:
            job = db.get(RelayJob, job_id)
            if not job:
                return
            account_id = job.account_id
        async with self.account_lock(account_id):
            await self._send_job(job_id)

    async def _send_job(self, job_id):
        if not self.running:
            return
        with session_scope() as db:
            job = db.get(RelayJob, job_id)
            if not job or job.status not in ("pending", "waiting"):
                return
            task = db.get(RelayTask, job.task_id)
            account = db.get(Account, job.account_id)
            if not task or not self.job_binding_valid(db, job, task):
                job.status, job.error = "cancelled", "群组绑定已失效"
                return
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
                entity = await asyncio.wait_for(client.get_entity("@" + username), timeout=SEND_TIMEOUT_SECONDS)
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
                    account = db.get(Account, job.account_id)
                    if not account or not account.send_enabled or not account.private_message_enabled or account.status not in ("active", "flood_wait"):
                        return
                    if account.flood_wait_until and account.flood_wait_until > datetime.utcnow():
                        job.status, job.due_at = "waiting", account.flood_wait_until
                        return
                    if not self.job_binding_valid(db, job, task):
                        job.status, job.error = "cancelled", "群组绑定已失效"
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
                if not self.job_binding_valid(db, job, task):
                    job.status, job.error = "cancelled", "群组绑定已失效"
                    return
                if stage == "dm" and not account.private_message_enabled:
                    return
                job.status = "sending"
                text = job.text
            # Disable implicit Markdown parsing so user text/mentions stay literal.
            sent = await asyncio.wait_for(client.send_message(
                destination, text, parse_mode=None, link_preview=False
            ), timeout=SEND_TIMEOUT_SECONDS)
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
