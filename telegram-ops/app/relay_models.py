"""Additive tables: existing upstream account data is preserved."""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    BigInteger,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import TimestampMixin


class RelayTask(Base, TimestampMixin):
    __tablename__ = "relay_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    account_a: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    account_b: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    source_chats: Mapped[str] = mapped_column(Text, default="[]")
    relay_chat: Mapped[int] = mapped_column(BigInteger)
    keywords: Mapped[str] = mapped_column(Text)
    exclude_keywords: Mapped[str] = mapped_column(Text, default="")
    ignore_users: Mapped[str] = mapped_column(Text, default="")
    match_mode: Mapped[str] = mapped_column(String(20), default="any")
    template: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class RelayJob(Base, TimestampMixin):
    __tablename__ = "relay_jobs"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "stage", "chat_id", "message_id", name="uq_relay_event"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("relay_tasks.id"))
    stage: Mapped[str] = mapped_column(String(16))  # relay / dm
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str] = mapped_column(String(64))
    source_title: Mapped[str] = mapped_column(String(255), default="")
    original_text: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ContactReceipt(Base):
    __tablename__ = "relay_contacts"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("relay_jobs.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConsoleState(Base):
    __tablename__ = "console_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AccountProfile(Base, TimestampMixin):
    __tablename__ = "account_profiles"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="monitor")
    monitor_chat_ids: Mapped[str] = mapped_column(Text, default="[]")


class SenderWeight(Base):
    __tablename__ = "sender_weights"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)
