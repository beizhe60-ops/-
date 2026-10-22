"""Independent channel collection, review and explicit publishing records."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import TimestampMixin


class ChannelFeed(Base, TimestampMixin):
    __tablename__ = 'channel_feeds'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    source: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_title: Mapped[str] = mapped_column(String(255), default='')
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    exclude_terms: Mapped[str] = mapped_column(Text, default='七天')
    ad_terms: Mapped[str] = mapped_column(Text, default='广告合作\n广告招商\n广告位招租\n付费推广\n商务合作\n推广合作')
    filter_ads: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')
    template_title: Mapped[str] = mapped_column(String(60), default='曝光档案', server_default='曝光档案')
    emoji_style: Mapped[str] = mapped_column(String(16), default='alert', server_default='alert')
    body_format: Mapped[str] = mapped_column(String(16), default='normal', server_default='normal')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChannelDraft(Base, TimestampMixin):
    __tablename__ = 'channel_drafts'
    __table_args__ = (UniqueConstraint('feed_id', 'source_id', 'event_key', name='uq_channel_post'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey('channel_feeds.id'), index=True)
    source_id: Mapped[int] = mapped_column(BigInteger)
    event_key: Mapped[str] = mapped_column(String(50))
    message_ids: Mapped[str] = mapped_column(Text)
    media: Mapped[str] = mapped_column(Text, default='[]')
    original_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    keep_media: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default='draft', index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_date: Mapped[datetime] = mapped_column(DateTime)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')
    config_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publish_target: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sent_ids: Mapped[str] = mapped_column(Text, default='[]')
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
