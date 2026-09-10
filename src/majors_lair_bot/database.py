from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import ActionType, EngagementAction, LinkedUser, ReconcileScope
from .orm import (
    ActionRow,
    AdminSessionRow,
    AuditRow,
    Base,
    ConfigRow,
    HistoricalSnapshotRow,
    ScanRunRow,
    TrackedPostRow,
    UserRow,
)
from .scoring import CONFIG_DESCRIPTIONS, DEFAULT_CONFIG
from .utils import isoformat, parse_bool, parse_datetime, utc_now


class DatabaseRepositoryError(RuntimeError):
    pass


class LinkConflictError(DatabaseRepositoryError):
    pass


def normalize_async_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


def create_database_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        normalize_async_database_url(database_url),
        echo=echo,
        pool_pre_ping=True,
    )


class DatabaseRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def close(self) -> None:
        await self.engine.dispose()

    async def ensure_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        now = utc_now()
        async with self.sessions.begin() as session:
            existing = set((await session.scalars(select(ConfigRow.key))).all())
            for key, value in DEFAULT_CONFIG.items():
                if key in existing:
                    continue
                session.add(
                    ConfigRow(
                        key=key,
                        value=value,
                        description=CONFIG_DESCRIPTIONS.get(
                            key, "Editable scoring or scan configuration."
                        ),
                        updated_at=now,
                        updated_by="system",
                    )
                )

    async def get_config(self) -> dict[str, str]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(ConfigRow))).all()
        return {row.key: row.value for row in rows}

    async def list_config_entries(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(ConfigRow).order_by(ConfigRow.key))).all()
        return [
            {
                "key": row.key,
                "value": row.value,
                "description": row.description,
                "updated_at": isoformat(row.updated_at),
                "updated_by": row.updated_by,
            }
            for row in rows
        ]

    async def set_config_values(
        self, changes: dict[str, str], *, actor_discord_id: str = "system"
    ) -> None:
        now = utc_now()
        async with self.sessions.begin() as session:
            for key, value in changes.items():
                row = await session.get(ConfigRow, key)
                if row is None:
                    row = ConfigRow(
                        key=key,
                        value=str(value),
                        description=CONFIG_DESCRIPTIONS.get(key, "Runtime configuration."),
                        updated_at=now,
                        updated_by=actor_discord_id,
                    )
                    session.add(row)
                else:
                    row.value = str(value)
                    row.updated_at = now
                    row.updated_by = actor_discord_id

    @staticmethod
    def _linked_user(row: UserRow) -> LinkedUser:
        return LinkedUser(
            discord_user_id=row.discord_user_id,
            discord_username=row.discord_username,
            twitter_handle=row.twitter_handle,
            twitter_user_id=row.twitter_user_id,
            linked_at=isoformat(row.linked_at),
            updated_at=isoformat(row.updated_at),
            active=row.active,
            score=float(row.score or 0),
            last_active_at=isoformat(row.last_active_at) if row.last_active_at else "",
            handle_history="|".join(row.handle_history or []),
        )

    async def list_users(self, *, active_only: bool = False) -> list[LinkedUser]:
        statement = select(UserRow)
        if active_only:
            statement = statement.where(UserRow.active.is_(True))
        statement = statement.order_by(UserRow.discord_username)
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._linked_user(row) for row in rows]

    async def link_user(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        twitter_handle: str,
        twitter_user_id: str,
    ) -> tuple[str, str]:
        handle = twitter_handle.removeprefix("@").lower()
        now = utc_now()
        async with self.sessions.begin() as session:
            duplicate = await session.scalar(
                select(UserRow)
                .where(
                    UserRow.active.is_(True),
                    UserRow.discord_user_id != discord_user_id,
                    or_(
                        UserRow.twitter_user_id == twitter_user_id,
                        func.lower(UserRow.twitter_handle) == handle,
                    ),
                )
                .with_for_update()
            )
            if duplicate is not None:
                raise LinkConflictError(
                    "That X account is already linked to another Discord member"
                )

            target = await session.get(UserRow, discord_user_id, with_for_update=True)
            old_handle = target.twitter_handle if target else ""
            if target is None:
                target = UserRow(
                    discord_user_id=discord_user_id,
                    discord_username=discord_username,
                    twitter_handle=handle,
                    twitter_user_id=twitter_user_id,
                    linked_at=now,
                    updated_at=now,
                    active=True,
                    score=0,
                    handle_history=[],
                )
                session.add(target)
            else:
                history = list(target.handle_history or [])
                if old_handle and old_handle != handle and old_handle not in history:
                    history.append(old_handle)
                target.discord_username = discord_username
                target.twitter_handle = handle
                target.twitter_user_id = twitter_user_id
                target.updated_at = now
                target.active = True
                target.handle_history = history
        return old_handle, handle

    async def unlink_user(self, discord_user_id: str) -> str:
        async with self.sessions.begin() as session:
            row = await session.get(UserRow, discord_user_id, with_for_update=True)
            if row is None or not row.active:
                return ""
            row.active = False
            row.updated_at = utc_now()
            return row.twitter_handle

    async def get_user(self, discord_user_id: str) -> LinkedUser | None:
        async with self.sessions() as session:
            row = await session.get(UserRow, discord_user_id)
        return self._linked_user(row) if row else None

    async def leaderboard(self, limit: int = 25) -> list[LinkedUser]:
        statement = (
            select(UserRow)
            .where(UserRow.active.is_(True))
            .order_by(UserRow.score.desc(), UserRow.discord_username)
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._linked_user(row) for row in rows]

    async def low_activity(self, threshold: float) -> list[LinkedUser]:
        statement = (
            select(UserRow)
            .where(UserRow.active.is_(True), UserRow.score <= threshold)
            .order_by(UserRow.score, UserRow.discord_username)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._linked_user(row) for row in rows]

    async def append_audit(
        self,
        *,
        event_type: str,
        actor_discord_id: str,
        subject_discord_id: str = "",
        old_value: str = "",
        new_value: str = "",
        details: dict[str, Any] | str = "",
    ) -> str:
        event_id = str(uuid.uuid4())
        payload = details if isinstance(details, dict) else {"message": details}
        async with self.sessions.begin() as session:
            session.add(
                AuditRow(
                    event_id=event_id,
                    event_type=event_type,
                    actor_discord_id=actor_discord_id,
                    subject_discord_id=subject_discord_id,
                    old_value=old_value,
                    new_value=new_value,
                    details=payload,
                    created_at=utc_now(),
                )
            )
        return event_id

    async def upsert_tracked_posts(self, posts: list[dict[str, Any]]) -> None:
        if not posts:
            return
        async with self.sessions.begin() as session:
            for post in posts:
                tweet_id = str(post["tweet_id"])
                row = await session.get(TrackedPostRow, tweet_id)
                data = {
                    "url": str(post.get("url", "")),
                    "source_handle": str(post.get("source_handle", "")).removeprefix("@").lower(),
                    "origin": str(post.get("origin", "auto")),
                    "active": parse_bool(post.get("active"), default=True),
                }
                discovered_at = parse_datetime(post.get("discovered_at"))
                post_created_at = parse_datetime(
                    post.get("post_created_at") or post.get("discovered_at")
                )
                last_checked_at = (
                    parse_datetime(post.get("last_checked_at"))
                    if post.get("last_checked_at")
                    else None
                )
                if row is None:
                    row = TrackedPostRow(
                        tweet_id=tweet_id,
                        discovered_at=discovered_at,
                        post_created_at=post_created_at,
                        last_checked_at=last_checked_at,
                        **data,
                    )
                    session.add(row)
                else:
                    for key in ("url", "source_handle", "origin", "active"):
                        if key in post:
                            setattr(row, key, data[key])
                    if post.get("last_checked_at"):
                        row.last_checked_at = last_checked_at
                    if post.get("post_created_at"):
                        row.post_created_at = post_created_at

    async def list_tracked_posts(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        statement = select(TrackedPostRow)
        if active_only:
            statement = statement.where(TrackedPostRow.active.is_(True))
        statement = statement.order_by(TrackedPostRow.post_created_at.desc())
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [
            {
                "tweet_id": row.tweet_id,
                "url": row.url,
                "source_handle": row.source_handle,
                "discovered_at": isoformat(row.discovered_at),
                "origin": row.origin,
                "active": row.active,
                "last_checked_at": isoformat(row.last_checked_at) if row.last_checked_at else "",
                "post_created_at": isoformat(row.post_created_at),
            }
            for row in rows
        ]

    @staticmethod
    def _action_from_row(row: ActionRow) -> EngagementAction:
        return EngagementAction(
            action_key=row.action_key,
            cycle_id=row.cycle_id,
            discord_user_id=row.discord_user_id,
            twitter_user_id=row.twitter_user_id,
            twitter_handle=row.twitter_handle,
            action_type=ActionType(row.action_type),
            target_handle=row.target_handle,
            source_post_id=row.source_post_id,
            action_tweet_id=row.action_tweet_id,
            action_url=row.action_url,
            text=row.text,
            normalized_text=row.normalized_text,
            content_hash=row.content_hash,
            has_media=row.has_media,
            occurred_at=isoformat(row.occurred_at),
            points=float(row.points or 0),
            reason=row.reason,
            active=row.active,
            first_seen_at=isoformat(row.first_seen_at),
            last_seen_at=isoformat(row.last_seen_at),
        )

    @staticmethod
    def _apply_action(row: ActionRow, action: EngagementAction) -> None:
        row.cycle_id = action.cycle_id
        row.discord_user_id = action.discord_user_id
        row.twitter_user_id = action.twitter_user_id
        row.twitter_handle = action.twitter_handle
        row.action_type = action.action_type.value
        row.target_handle = action.target_handle
        row.source_post_id = action.source_post_id
        row.action_tweet_id = action.action_tweet_id
        row.action_url = action.action_url
        row.text = action.text
        row.normalized_text = action.normalized_text
        row.content_hash = action.content_hash
        row.has_media = action.has_media
        row.occurred_at = parse_datetime(action.occurred_at)
        row.points = action.points
        row.reason = action.reason
        row.active = action.active
        row.first_seen_at = parse_datetime(action.first_seen_at)
        row.last_seen_at = parse_datetime(action.last_seen_at)

    async def list_actions(
        self, *, cycle_id: str | None = None, include_inactive: bool = True
    ) -> list[EngagementAction]:
        statement = select(ActionRow)
        if cycle_id is not None:
            statement = statement.where(ActionRow.cycle_id == cycle_id)
        if not include_inactive:
            statement = statement.where(ActionRow.active.is_(True))
        statement = statement.order_by(ActionRow.occurred_at.desc())
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._action_from_row(row) for row in rows]

    @staticmethod
    def _scope_matches(action: ActionRow, scope: ReconcileScope) -> bool:
        if action.action_type != scope.action_type.value:
            return False
        if action.target_handle.lower() != scope.target_handle.lower():
            return False
        if scope.source_post_id and action.source_post_id != scope.source_post_id:
            return False
        occurred_at = parse_datetime(action.occurred_at)
        if scope.since_iso and occurred_at < parse_datetime(scope.since_iso):
            return False
        return not (scope.until_iso and occurred_at > parse_datetime(scope.until_iso))

    async def reconcile_actions(
        self,
        *,
        cycle_id: str,
        discovered: list[EngagementAction],
        scopes: list[ReconcileScope],
    ) -> int:
        now = utc_now()
        discovered_keys = {action.action_key for action in discovered}
        complete_scopes = [scope for scope in scopes if scope.complete]
        changed = 0
        async with self.sessions.begin() as session:
            current_rows = (
                await session.scalars(
                    select(ActionRow).where(ActionRow.cycle_id == cycle_id).with_for_update()
                )
            ).all()
            indexed = {row.action_key: row for row in current_rows}
            for row in current_rows:
                if not row.active or row.action_key in discovered_keys:
                    continue
                if any(self._scope_matches(row, scope) for scope in complete_scopes):
                    row.active = False
                    row.last_seen_at = now
                    changed += 1

            for candidate in discovered:
                row = indexed.get(candidate.action_key)
                if row is None:
                    candidate.first_seen_at = isoformat(now)
                    candidate.last_seen_at = isoformat(now)
                    row = ActionRow(
                        action_key=candidate.action_key,
                        cycle_id=candidate.cycle_id,
                        discord_user_id=candidate.discord_user_id,
                        twitter_user_id=candidate.twitter_user_id,
                        twitter_handle=candidate.twitter_handle,
                        action_type=candidate.action_type.value,
                        target_handle=candidate.target_handle,
                        source_post_id=candidate.source_post_id,
                        action_tweet_id=candidate.action_tweet_id,
                        action_url=candidate.action_url,
                        text=candidate.text,
                        normalized_text=candidate.normalized_text,
                        content_hash=candidate.content_hash,
                        has_media=candidate.has_media,
                        occurred_at=parse_datetime(candidate.occurred_at),
                        points=candidate.points,
                        reason=candidate.reason,
                        active=True,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(row)
                    indexed[candidate.action_key] = row
                    changed += 1
                else:
                    candidate.first_seen_at = isoformat(row.first_seen_at)
                    if candidate.action_type == ActionType.RETWEET:
                        candidate.occurred_at = isoformat(row.occurred_at)
                    candidate.last_seen_at = isoformat(now)
                    candidate.active = True
                    if not row.active:
                        changed += 1
                    self._apply_action(row, candidate)
        return changed

    async def save_scored_actions(
        self, *, cycle_id: str, scored_actions: list[EngagementAction]
    ) -> None:
        totals: dict[str, float] = {}
        latest: dict[str, datetime] = {}
        for action in scored_actions:
            if action.cycle_id != cycle_id or not action.active:
                continue
            totals[action.discord_user_id] = totals.get(action.discord_user_id, 0) + action.points
            occurred_at = parse_datetime(action.occurred_at)
            if action.points > 0 and occurred_at > latest.get(
                action.discord_user_id, datetime.fromtimestamp(0, tz=UTC)
            ):
                latest[action.discord_user_id] = occurred_at

        async with self.sessions.begin() as session:
            for action in scored_actions:
                row = await session.get(ActionRow, action.action_key)
                if row is not None:
                    self._apply_action(row, action)
            users = (await session.scalars(select(UserRow))).all()
            for user in users:
                user.score = round(totals.get(user.discord_user_id, 0), 2)
                user.last_active_at = latest.get(user.discord_user_id)

    async def user_history(
        self, discord_user_id: str, cycle_id: str, limit: int = 10
    ) -> list[EngagementAction]:
        statement = (
            select(ActionRow)
            .where(
                ActionRow.discord_user_id == discord_user_id,
                ActionRow.cycle_id == cycle_id,
            )
            .order_by(ActionRow.occurred_at.desc())
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._action_from_row(row) for row in rows]

    async def reset_leaderboard(self, actor_discord_id: str) -> tuple[str, str, int]:
        reset_at = utc_now()
        new_cycle = f"cycle_{reset_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        snapshot_id = str(uuid.uuid4())
        async with self.sessions.begin() as session:
            current_cycle = await session.get(ConfigRow, "current_cycle_id", with_for_update=True)
            if current_cycle is None:
                raise DatabaseRepositoryError("current_cycle_id configuration is missing")
            old_cycle = current_cycle.value
            users = (
                await session.scalars(
                    select(UserRow)
                    .where(UserRow.active.is_(True))
                    .order_by(UserRow.score.desc(), UserRow.discord_username)
                    .with_for_update()
                )
            ).all()
            for rank, user in enumerate(users, start=1):
                session.add(
                    HistoricalSnapshotRow(
                        row_id=str(uuid.uuid4()),
                        snapshot_id=snapshot_id,
                        cycle_id=old_cycle,
                        discord_user_id=user.discord_user_id,
                        discord_username=user.discord_username,
                        twitter_handle=user.twitter_handle,
                        score=float(user.score or 0),
                        rank=rank,
                        reset_by_discord_id=actor_discord_id,
                        reset_at=reset_at,
                    )
                )
            all_users = (await session.scalars(select(UserRow).with_for_update())).all()
            for user in all_users:
                user.score = 0
                user.last_active_at = None
            current_cycle.value = new_cycle
            current_cycle.updated_at = reset_at
            current_cycle.updated_by = actor_discord_id
            cycle_started = await session.get(ConfigRow, "cycle_started_at")
            if cycle_started is None:
                session.add(
                    ConfigRow(
                        key="cycle_started_at",
                        value=isoformat(reset_at),
                        description=CONFIG_DESCRIPTIONS["cycle_started_at"],
                        updated_at=reset_at,
                        updated_by=actor_discord_id,
                    )
                )
            else:
                cycle_started.value = isoformat(reset_at)
                cycle_started.updated_at = reset_at
                cycle_started.updated_by = actor_discord_id
        return old_cycle, new_cycle, len(users)

    async def overview(self) -> dict[str, Any]:
        async with self.sessions() as session:
            cycle = await session.get(ConfigRow, "current_cycle_id")
            linked_count, total_score, action_count, tracked_count = await self._overview_counts(
                session, cycle.value if cycle else ""
            )
            last_scan = await session.scalar(
                select(ScanRunRow).order_by(ScanRunRow.started_at.desc()).limit(1)
            )
        return {
            "linked_members": linked_count,
            "total_score": round(float(total_score or 0), 2),
            "active_actions": action_count,
            "tracked_posts": tracked_count,
            "cycle_id": cycle.value if cycle else "",
            "last_scan": self._scan_dict(last_scan) if last_scan else None,
        }

    @staticmethod
    async def _overview_counts(session: AsyncSession, cycle_id: str) -> tuple[int, float, int, int]:
        linked_count = int(
            await session.scalar(
                select(func.count()).select_from(UserRow).where(UserRow.active.is_(True))
            )
            or 0
        )
        total_score = float(
            await session.scalar(
                select(func.coalesce(func.sum(UserRow.score), 0)).where(UserRow.active.is_(True))
            )
            or 0
        )
        action_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ActionRow)
                .where(ActionRow.active.is_(True))
                .where(ActionRow.cycle_id == cycle_id)
            )
            or 0
        )
        tracked_count = int(
            await session.scalar(
                select(func.count())
                .select_from(TrackedPostRow)
                .where(TrackedPostRow.active.is_(True))
            )
            or 0
        )
        return linked_count, total_score, action_count, tracked_count

    async def paginated_users(
        self,
        *,
        search: str = "",
        active: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        filters = []
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    UserRow.discord_username.ilike(pattern),
                    UserRow.twitter_handle.ilike(pattern),
                    UserRow.discord_user_id.ilike(pattern),
                )
            )
        if active is not None:
            filters.append(UserRow.active.is_(active))
        count_statement = select(func.count()).select_from(UserRow).where(*filters)
        statement = (
            select(UserRow)
            .where(*filters)
            .order_by(UserRow.score.desc(), UserRow.discord_username)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        async with self.sessions() as session:
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.scalars(statement)).all()
        return {
            "items": [asdict(self._linked_user(row)) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def set_user_active(self, discord_user_id: str, active: bool) -> LinkedUser | None:
        async with self.sessions.begin() as session:
            row = await session.get(UserRow, discord_user_id, with_for_update=True)
            if row is None:
                return None
            if active and not row.active:
                duplicate = await session.scalar(
                    select(UserRow).where(
                        UserRow.active.is_(True),
                        UserRow.discord_user_id != discord_user_id,
                        or_(
                            UserRow.twitter_user_id == row.twitter_user_id,
                            func.lower(UserRow.twitter_handle) == row.twitter_handle.lower(),
                        ),
                    )
                )
                if duplicate:
                    raise LinkConflictError(
                        "That X account is currently linked to another active member"
                    )
            row.active = active
            row.updated_at = utc_now()
        return self._linked_user(row)

    async def paginated_actions(
        self,
        *,
        action_type: str = "",
        active: bool | None = None,
        search: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        filters = []
        if action_type:
            filters.append(ActionRow.action_type == action_type)
        if active is not None:
            filters.append(ActionRow.active.is_(active))
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    ActionRow.twitter_handle.ilike(pattern),
                    ActionRow.text.ilike(pattern),
                    ActionRow.reason.ilike(pattern),
                )
            )
        async with self.sessions() as session:
            current_cycle = await session.get(ConfigRow, "current_cycle_id")
            if current_cycle:
                filters.append(ActionRow.cycle_id == current_cycle.value)
            count_statement = select(func.count()).select_from(ActionRow).where(*filters)
            statement = (
                select(ActionRow)
                .where(*filters)
                .order_by(ActionRow.occurred_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.scalars(statement)).all()
        return {
            "items": [asdict(self._action_from_row(row)) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def paginated_audit(
        self, *, event_type: str = "", page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        filters = [AuditRow.event_type == event_type] if event_type else []
        count_statement = select(func.count()).select_from(AuditRow).where(*filters)
        statement = (
            select(AuditRow)
            .where(*filters)
            .order_by(AuditRow.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        async with self.sessions() as session:
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.scalars(statement)).all()
        return {
            "items": [
                {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "actor_discord_id": row.actor_discord_id,
                    "subject_discord_id": row.subject_discord_id,
                    "old_value": row.old_value,
                    "new_value": row.new_value,
                    "details": row.details,
                    "created_at": isoformat(row.created_at),
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    @staticmethod
    def _scan_dict(row: ScanRunRow) -> dict[str, Any]:
        return {
            "scan_id": row.scan_id,
            "period": row.period,
            "status": row.status,
            "triggered_by": row.triggered_by,
            "source": row.source,
            "started_at": isoformat(row.started_at),
            "completed_at": isoformat(row.completed_at) if row.completed_at else "",
            "summary": row.summary,
            "error": row.error,
        }

    async def create_scan_run(self, *, period: str, triggered_by: str, source: str) -> str:
        scan_id = str(uuid.uuid4())
        async with self.sessions.begin() as session:
            session.add(
                ScanRunRow(
                    scan_id=scan_id,
                    period=period,
                    status="running",
                    triggered_by=triggered_by,
                    source=source,
                    started_at=utc_now(),
                    summary={},
                    error="",
                )
            )
        return scan_id

    async def finish_scan_run(
        self,
        scan_id: str,
        *,
        summary: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(ScanRunRow, scan_id, with_for_update=True)
            if row is None:
                return
            row.status = "failed" if error else "complete"
            row.completed_at = utc_now()
            row.summary = summary or {}
            row.error = error[:4000]

    async def recent_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ScanRunRow).order_by(ScanRunRow.started_at.desc()).limit(limit)
                )
            ).all()
        return [self._scan_dict(row) for row in rows]

    @staticmethod
    def hash_session_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create_admin_session(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        avatar_url: str,
        role_ids: list[str],
        is_guild_admin: bool,
        ttl_hours: int,
    ) -> tuple[str, str]:
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = utc_now()
        async with self.sessions.begin() as session:
            session.add(
                AdminSessionRow(
                    token_hash=self.hash_session_token(token),
                    discord_user_id=discord_user_id,
                    discord_username=discord_username,
                    avatar_url=avatar_url,
                    role_ids=role_ids,
                    is_guild_admin=is_guild_admin,
                    csrf_token=csrf_token,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(hours=ttl_hours),
                )
            )
        return token, csrf_token

    async def get_admin_session(self, token: str) -> dict[str, Any] | None:
        token_hash = self.hash_session_token(token)
        now = utc_now()
        async with self.sessions.begin() as session:
            await session.execute(delete(AdminSessionRow).where(AdminSessionRow.expires_at <= now))
            row = await session.get(AdminSessionRow, token_hash)
            if row is None:
                return None
            row.last_seen_at = now
            return {
                "discord_user_id": row.discord_user_id,
                "discord_username": row.discord_username,
                "avatar_url": row.avatar_url,
                "role_ids": list(row.role_ids or []),
                "is_guild_admin": row.is_guild_admin,
                "csrf_token": row.csrf_token,
                "expires_at": isoformat(row.expires_at),
            }

    async def delete_admin_session(self, token: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(AdminSessionRow).where(
                    AdminSessionRow.token_hash == self.hash_session_token(token)
                )
            )
