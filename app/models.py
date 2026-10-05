"""Portable schema. All timestamps are UTC; local day is materialized at intake."""

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON, CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index,
    Integer, MetaData, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite drops tzinfo: store naive UTC there and restore it on reads."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timezone-aware datetime required")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class User(Base):
    """Minimal identity; no profile harvesting, presence, or activity telemetry."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("platform", "workspace_id", "external_user_id", name="uq_user_external"),
        UniqueConstraint("id", "platform", "workspace_id", name="uq_user_scope"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str] = mapped_column(String(64))
    external_user_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    display_name: Mapped[str | None] = mapped_column(String(80))


class StandupEntry(Base):
    """Immutable accepted update. Original content is never LLM-generated."""

    __tablename__ = "standup_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "platform", "workspace_id"],
            ["users.id", "users.platform", "users.workspace_id"], ondelete="CASCADE",
        ),
        UniqueConstraint("platform", "workspace_id", "source_id", name="uq_entry_source"),
        UniqueConstraint("id", "platform", "workspace_id", "channel_id", name="uq_entry_scope"),
        CheckConstraint("length(raw_text) BETWEEN 1 AND 4000", name="text_length"),
        CheckConstraint("input_kind IN ('command', 'message')", name="input_kind"),
        Index("ix_entries_daily_scope", "platform", "workspace_id", "channel_id", "standup_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    platform: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(64))
    input_kind: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    standup_date: Mapped[date] = mapped_column(Date)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class DailyDigest(Base):
    """One digest per scope/day; worker must atomically claim and lease the row."""

    __tablename__ = "daily_digests"
    __table_args__ = (
        UniqueConstraint("platform", "workspace_id", "channel_id", "digest_date", name="uq_digest_day"),
        UniqueConstraint("id", "platform", "workspace_id", "channel_id", name="uq_digest_scope"),
        CheckConstraint("status IN ('pending', 'processing', 'ready', 'publishing', 'published', 'failed', 'review')", name="status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(64))
    digest_date: Mapped[date] = mapped_column(Date)
    timezone_name: Mapped[str] = mapped_column(String(64))
    cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(20), default="pending")
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    input_hash: Mapped[str | None] = mapped_column(String(64))
    structured_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    posted_message_id: Mapped[str | None] = mapped_column(String(128))
    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DigestSource(Base):
    """Frozen membership; composite FKs prevent cross-channel/workspace evidence."""

    __tablename__ = "digest_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["digest_id", "platform", "workspace_id", "channel_id"],
            ["daily_digests.id", "daily_digests.platform", "daily_digests.workspace_id", "daily_digests.channel_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entry_id", "entry_platform", "workspace_id", "channel_id"],
            ["standup_entries.id", "standup_entries.platform", "standup_entries.workspace_id", "standup_entries.channel_id"],
            ondelete="CASCADE",
        ),
    )
    digest_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entry_platform: Mapped[str] = mapped_column(String(20), default="slack")
    platform: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(64))


class TrackerSync(Base):
    """One logical task and its durable tracker sync state."""

    __tablename__ = "tracker_sync"
    __table_args__ = (
        ForeignKeyConstraint(
            ["digest_id", "platform", "workspace_id", "channel_id"],
            ["daily_digests.id", "daily_digests.platform", "daily_digests.workspace_id", "daily_digests.channel_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("platform", "workspace_id", "tracker_target", "item_key", name="uq_tracker_item"),
        UniqueConstraint("id", "platform", "workspace_id", "channel_id", name="uq_task_scope"),
        CheckConstraint("status IN ('pending', 'processing', 'synced', 'failed', 'review')", name="status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    digest_id: Mapped[str] = mapped_column(String(36))
    platform: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(64))
    tracker_target: Mapped[str] = mapped_column(String(128))
    item_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    external_ticket_url: Mapped[str | None] = mapped_column(Text)
    board_status: Mapped[str] = mapped_column(String(20), default="todo")
    due_date: Mapped[date | None] = mapped_column(Date)
    deadline_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deadline_updated_by: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    external_ticket_id: Mapped[str | None] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class TaskOccurrence(Base):
    """One evidenced report of a task; replaying a job never adds a report."""

    __tablename__ = "task_occurrences"
    __table_args__ = (
        UniqueConstraint("task_id", "entry_id", name="uq_task_occurrence_source"),
        ForeignKeyConstraint(
            ["task_id", "platform", "workspace_id", "channel_id"],
            ["tracker_sync.id", "tracker_sync.platform", "tracker_sync.workspace_id", "tracker_sync.channel_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["digest_id", "platform", "workspace_id", "channel_id"],
            ["daily_digests.id", "daily_digests.platform", "daily_digests.workspace_id", "daily_digests.channel_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entry_id", "entry_platform", "workspace_id", "channel_id"],
            ["standup_entries.id", "standup_entries.platform", "standup_entries.workspace_id", "standup_entries.channel_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["digest_id", "entry_id"], ["digest_sources.digest_id", "digest_sources.entry_id"], ondelete="CASCADE"),
        CheckConstraint("length(quote) BETWEEN 1 AND 4000", name="quote_length"),
        Index("ix_task_occurrence_history", "task_id", "recorded_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(36))
    digest_id: Mapped[str] = mapped_column(String(36))
    entry_id: Mapped[str] = mapped_column(String(36))
    entry_platform: Mapped[str] = mapped_column(String(20))
    platform: Mapped[str] = mapped_column(String(20))
    workspace_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(64))
    quote: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
