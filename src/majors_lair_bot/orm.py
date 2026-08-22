from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    discord_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    discord_username: Mapped[str] = mapped_column(String(120), nullable=False)
    twitter_handle: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    twitter_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handle_history: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index("ix_users_active_score", "active", "score"),
        Index("ix_users_active_twitter", "active", "twitter_user_id"),
        Index(
            "uq_users_active_twitter_id",
            "twitter_user_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
    )


class ActionRow(Base):
    __tablename__ = "actions_log"

    action_key: Mapped[str] = mapped_column(String(320), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    discord_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    twitter_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    twitter_handle: Mapped[str] = mapped_column(String(32), nullable=False)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    target_handle: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_post_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    action_tweet_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    action_url: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    has_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    points: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_actions_cycle_active", "cycle_id", "active"),
        Index("ix_actions_source_scope", "cycle_id", "source_post_id", "action_type"),
        Index("ix_actions_member_cycle", "discord_user_id", "cycle_id", "occurred_at"),
    )


class AuditRow(Base):
    __tablename__ = "audit_log"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor_discord_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    subject_discord_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    old_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    new_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class TrackedPostRow(Base):
    __tablename__ = "tracked_posts"

    tweet_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str] = mapped_column(String(300), nullable=False)
    source_handle: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigRow(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(32), nullable=False, default="system")


class HistoricalSnapshotRow(Base):
    __tablename__ = "historical_snapshots"

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    discord_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    discord_username: Mapped[str] = mapped_column(String(120), nullable=False)
    twitter_handle: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    reset_by_discord_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScanRunRow(Base):
    __tablename__ = "scan_runs"

    scan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    triggered_by: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="discord")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AdminSessionRow(Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    discord_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    discord_username: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    role_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_guild_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
