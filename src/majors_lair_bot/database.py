from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
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
    ScoreAdjustmentRow,
    TrackedPostRow,
    UserRow,
)
from .scoring import CONFIG_DESCRIPTIONS, DEFAULT_CONFIG
from .utils import isoformat, parse_bool, parse_datetime, utc_now


# The sort orders the Activity log offers on its column headers. Anything else falls back
# to newest first, so a hand-edited query string cannot produce an unordered page.
ACTION_ORDERS = {
    "occurred_desc": ActionRow.occurred_at.desc(),
    "occurred_asc": ActionRow.occurred_at.asc(),
    "points_desc": ActionRow.points.desc(),
    "points_asc": ActionRow.points.asc(),
    "member": ActionRow.twitter_handle.asc(),
    "type": ActionRow.action_type.asc(),
}


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
            rows = {row.key: row for row in (await session.scalars(select(ConfigRow))).all()}
            for key, value in DEFAULT_CONFIG.items():
                description = CONFIG_DESCRIPTIONS.get(
                    key, "Editable scoring or scan configuration."
                )
                row = rows.get(key)
                if row is None:
                    session.add(
                        ConfigRow(
                            key=key,
                            value=value,
                            description=description,
                            updated_at=now,
                            updated_by="system",
                        )
                    )
                    continue
                # The description belongs to the code, not to the database: nobody edits it
                # from the dashboard. Without this, a setting written before its explanation
                # existed would keep the old placeholder text forever.
                if row.description != description:
                    row.description = description

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
            twitter_handle=row.twitter_handle or "",
            twitter_user_id=row.twitter_user_id or "",
            linked_at=isoformat(row.linked_at),
            updated_at=isoformat(row.updated_at),
            active=row.active,
            score=float(row.score or 0),
            last_active_at=isoformat(row.last_active_at) if row.last_active_at else "",
            handle_history="|".join(row.handle_history or []),
            special_role=bool(row.special_role),
            special_role_names=row.special_role_names or "",
            x_status=row.x_status or "",
            x_checked_at=isoformat(row.x_checked_at) if row.x_checked_at else "",
            discord_joined_at=isoformat(row.discord_joined_at) if row.discord_joined_at else "",
        )

    async def list_users(self, *, active_only: bool = False) -> list[LinkedUser]:
        statement = select(UserRow)
        if active_only:
            statement = statement.where(UserRow.active.is_(True))
        statement = statement.order_by(UserRow.discord_username)
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._linked_user(row) for row in rows]

    async def register_member(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        special_role: bool | None = None,
        special_role_names: str | None = None,
        discord_joined_at: datetime | None = None,
    ) -> tuple[LinkedUser, bool]:
        """Make sure a member exists in the registry, with or without an X account.

        Returns the member and whether the row was created. Existing rows keep their X link
        and active flag; only the display name and the special-role fields are refreshed.
        """
        now = utc_now()
        async with self.sessions.begin() as session:
            row = await session.get(UserRow, discord_user_id, with_for_update=True)
            created = row is None
            if row is None:
                row = UserRow(
                    discord_user_id=discord_user_id,
                    discord_username=discord_username or discord_user_id,
                    twitter_handle=None,
                    twitter_user_id=None,
                    linked_at=now,
                    updated_at=now,
                    active=True,
                    score=0,
                    handle_history=[],
                    special_role=bool(special_role),
                    special_role_names=(special_role_names or "").strip()[:255],
                    discord_joined_at=discord_joined_at,
                )
                session.add(row)
            else:
                if discord_username:
                    row.discord_username = discord_username
                if special_role is not None:
                    row.special_role = special_role
                if special_role_names is not None:
                    row.special_role_names = special_role_names.strip()[:255]
                if discord_joined_at is not None:
                    row.discord_joined_at = discord_joined_at
                row.updated_at = now
            await session.flush()
            user = self._linked_user(row)
        return user, created

    async def record_x_verification(self, records: list[dict[str, str]]) -> int:
        """Store the outcome of an X account check; returns how many handles were renamed.

        Each record has ``discord_user_id`` and ``status`` and optionally ``twitter_handle``
        when X now reports a different handle for the same stable account ID.
        """
        renamed = 0
        now = utc_now()
        async with self.sessions.begin() as session:
            for record in records:
                row = await session.get(UserRow, record["discord_user_id"], with_for_update=True)
                if row is None:
                    continue
                row.x_status = record.get("status", "")[:32]
                row.x_checked_at = now
                new_handle = (record.get("twitter_handle") or "").lower()
                if new_handle and new_handle != (row.twitter_handle or "").lower():
                    history = list(row.handle_history or [])
                    if row.twitter_handle and row.twitter_handle not in history:
                        history.append(row.twitter_handle)
                    row.handle_history = history
                    row.twitter_handle = new_handle
                    renamed += 1
                row.updated_at = now
        return renamed

    async def set_special_role(
        self,
        discord_user_id: str,
        *,
        special_role: bool,
        special_role_names: str | None = None,
    ) -> LinkedUser | None:
        async with self.sessions.begin() as session:
            row = await session.get(UserRow, discord_user_id, with_for_update=True)
            if row is None:
                return None
            row.special_role = special_role
            if special_role_names is not None:
                row.special_role_names = special_role_names.strip()[:255]
            elif not special_role:
                row.special_role_names = ""
            row.updated_at = utc_now()
            return self._linked_user(row)

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
            old_handle = (target.twitter_handle or "") if target else ""
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
            if row is None or not row.active or not row.twitter_user_id:
                return ""
            row.active = False
            row.updated_at = utc_now()
            return row.twitter_handle or ""

    async def get_user(self, discord_user_id: str) -> LinkedUser | None:
        async with self.sessions() as session:
            row = await session.get(UserRow, discord_user_id)
        return self._linked_user(row) if row else None

    async def leaderboard(self, limit: int = 25) -> list[LinkedUser]:
        statement = (
            select(UserRow)
            .where(UserRow.active.is_(True), UserRow.twitter_user_id.is_not(None))
            .order_by(UserRow.score.desc(), UserRow.discord_username)
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._linked_user(row) for row in rows]

    async def leaderboard_window(
        self, *, cycle_id: str, since: datetime, limit: int = 25
    ) -> list[LinkedUser]:
        """Rank linked members by points earned on actions that happened since ``since``.

        Uses the stored per-action points, so it reflects exactly what scans have scored;
        activity that no scan covered yet is not there.
        """
        statement = (
            select(ActionRow.discord_user_id, func.coalesce(func.sum(ActionRow.points), 0))
            .where(
                ActionRow.cycle_id == cycle_id,
                ActionRow.active.is_(True),
                ActionRow.occurred_at >= since,
            )
            .group_by(ActionRow.discord_user_id)
        )
        adjustment_statement = (
            select(
                ScoreAdjustmentRow.discord_user_id,
                func.coalesce(func.sum(ScoreAdjustmentRow.points), 0),
            )
            .where(ScoreAdjustmentRow.cycle_id == cycle_id, ScoreAdjustmentRow.created_at >= since)
            .group_by(ScoreAdjustmentRow.discord_user_id)
        )
        async with self.sessions() as session:
            sums = {row[0]: float(row[1] or 0) for row in (await session.execute(statement)).all()}
            for row in (await session.execute(adjustment_statement)).all():
                sums[row[0]] = sums.get(row[0], 0.0) + float(row[1] or 0)
            rows = (
                await session.scalars(
                    select(UserRow).where(
                        UserRow.active.is_(True), UserRow.twitter_user_id.is_not(None)
                    )
                )
            ).all()
        ranked = []
        for row in rows:
            user = self._linked_user(row)
            user.score = round(sums.get(row.discord_user_id, 0.0), 2)
            ranked.append(user)
        ranked.sort(key=lambda user: (-user.score, user.discord_username.lower()))
        return ranked[:limit]

    async def low_activity(
        self,
        threshold: float,
        *,
        include_protected: bool = False,
        grace_days: int = 0,
    ) -> list[LinkedUser]:
        """Active members at or below the threshold, unlinked members first (score 0).

        Special-role members are left out unless ``include_protected`` is set. Members who
        joined the Discord server fewer than ``grace_days`` ago are left out too.
        """
        filters = [UserRow.active.is_(True), UserRow.score <= threshold]
        if not include_protected:
            filters.append(UserRow.special_role.is_(False))
        if grace_days > 0:
            cutoff = utc_now() - timedelta(days=grace_days)
            filters.append(
                or_(UserRow.discord_joined_at.is_(None), UserRow.discord_joined_at <= cutoff)
            )
        statement = (
            select(UserRow)
            .where(*filters)
            .order_by(UserRow.score, UserRow.twitter_user_id.is_not(None), UserRow.discord_username)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._linked_user(row) for row in rows]

    async def low_activity_report(self, threshold: float, *, grace_days: int = 0) -> dict[str, Any]:
        """Removal candidates plus how many members each protection rule kept off the list."""
        base = [UserRow.active.is_(True), UserRow.score <= threshold]
        newcomer = None
        if grace_days > 0:
            cutoff = utc_now() - timedelta(days=grace_days)
            newcomer = and_(
                UserRow.discord_joined_at.is_not(None), UserRow.discord_joined_at > cutoff
            )

        def count(*extra: Any) -> Any:
            return select(func.count()).select_from(UserRow).where(*base, *extra)

        filters = [*base, UserRow.special_role.is_(False)]
        if newcomer is not None:
            filters.append(~newcomer)
        async with self.sessions() as session:
            protected = int(await session.scalar(count(UserRow.special_role.is_(True))) or 0)
            newcomers = 0
            if newcomer is not None:
                newcomers = int(
                    await session.scalar(count(UserRow.special_role.is_(False), newcomer)) or 0
                )
            rows = (
                await session.scalars(
                    select(UserRow)
                    .where(*filters)
                    .order_by(
                        UserRow.score,
                        UserRow.twitter_user_id.is_not(None),
                        UserRow.discord_username,
                    )
                )
            ).all()
        return {
            "threshold": threshold,
            "newcomer_grace_days": grace_days,
            "excluded_protected": protected,
            "excluded_newcomers": newcomers,
            "items": [asdict(self._linked_user(row)) for row in rows],
        }

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
        ignore_discord_ids: set[str] | None = None,
        still_public: Callable[[list[str]], Awaitable[set[str]]] | None = None,
    ) -> int:
        """Merge freshly discovered actions into the cycle log.

        Rows belonging to ``ignore_discord_ids`` (members deliberately left out of this
        scan) are never deactivated, so skipping protected members freezes their history
        instead of erasing it. When ``still_public`` is given, a tweet that vanished from
        a fully scanned scope is only deactivated once X confirms it is gone; hidden
        replies (found earlier by the sweep or a member scan) therefore keep their points.
        """
        now = utc_now()
        ignored = ignore_discord_ids or set()
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
            candidates = [
                row
                for row in current_rows
                if row.active
                and row.action_key not in discovered_keys
                and row.discord_user_id not in ignored
                and any(self._scope_matches(row, scope) for scope in complete_scopes)
            ]
            confirmed_alive: set[str] = set()
            if still_public is not None:
                ids = sorted({row.action_tweet_id for row in candidates if row.action_tweet_id})
                if ids:
                    confirmed_alive = await still_public(ids)
            for row in candidates:
                if row.action_tweet_id and row.action_tweet_id in confirmed_alive:
                    row.last_seen_at = now  # hidden by X, but still public: keep it
                    continue
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
            adjustments = {
                row[0]: float(row[1] or 0)
                for row in (
                    await session.execute(
                        select(
                            ScoreAdjustmentRow.discord_user_id,
                            func.coalesce(func.sum(ScoreAdjustmentRow.points), 0),
                        )
                        .where(ScoreAdjustmentRow.cycle_id == cycle_id)
                        .group_by(ScoreAdjustmentRow.discord_user_id)
                    )
                ).all()
            }
            users = (await session.scalars(select(UserRow))).all()
            for user in users:
                user.score = round(
                    totals.get(user.discord_user_id, 0) + adjustments.get(user.discord_user_id, 0),
                    2,
                )
                user.last_active_at = latest.get(user.discord_user_id)

    @staticmethod
    def _adjustment_dict(row: ScoreAdjustmentRow) -> dict[str, Any]:
        return {
            "adjustment_id": row.adjustment_id,
            "cycle_id": row.cycle_id,
            "discord_user_id": row.discord_user_id,
            "points": float(row.points),
            "reason": row.reason,
            "actor_discord_id": row.actor_discord_id,
            "counterpart_discord_id": row.counterpart_discord_id,
            "transfer_id": row.transfer_id,
            "created_at": isoformat(row.created_at),
        }

    async def adjust_points(
        self,
        *,
        cycle_id: str,
        discord_user_id: str,
        points: float,
        reason: str,
        actor_discord_id: str,
        transfer_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Add or remove points for a member, or move them to ``transfer_to``.

        Adjustments live in their own table so scans and rescoring never touch them. The
        members' cached scores are updated immediately.
        """
        if points == 0:
            raise DatabaseRepositoryError("Amount must not be zero")
        now = utc_now()
        rows: list[ScoreAdjustmentRow] = []
        transfer_id = str(uuid.uuid4()) if transfer_to else ""
        async with self.sessions.begin() as session:
            source = await session.get(UserRow, discord_user_id, with_for_update=True)
            if source is None:
                raise DatabaseRepositoryError("Member not found")
            target = None
            if transfer_to:
                if transfer_to == discord_user_id:
                    raise DatabaseRepositoryError("Cannot transfer points to the same member")
                target = await session.get(UserRow, transfer_to, with_for_update=True)
                if target is None:
                    raise DatabaseRepositoryError("Receiving member not found")
                amount = abs(points)
                rows.append(
                    ScoreAdjustmentRow(
                        adjustment_id=str(uuid.uuid4()),
                        cycle_id=cycle_id,
                        discord_user_id=discord_user_id,
                        points=-amount,
                        reason=reason[:300],
                        actor_discord_id=actor_discord_id,
                        counterpart_discord_id=transfer_to,
                        transfer_id=transfer_id,
                        created_at=now,
                    )
                )
                rows.append(
                    ScoreAdjustmentRow(
                        adjustment_id=str(uuid.uuid4()),
                        cycle_id=cycle_id,
                        discord_user_id=transfer_to,
                        points=amount,
                        reason=reason[:300],
                        actor_discord_id=actor_discord_id,
                        counterpart_discord_id=discord_user_id,
                        transfer_id=transfer_id,
                        created_at=now,
                    )
                )
                source.score = round(float(source.score or 0) - amount, 2)
                target.score = round(float(target.score or 0) + amount, 2)
                target.updated_at = now
            else:
                rows.append(
                    ScoreAdjustmentRow(
                        adjustment_id=str(uuid.uuid4()),
                        cycle_id=cycle_id,
                        discord_user_id=discord_user_id,
                        points=points,
                        reason=reason[:300],
                        actor_discord_id=actor_discord_id,
                        created_at=now,
                    )
                )
                source.score = round(float(source.score or 0) + points, 2)
            source.updated_at = now
            for row in rows:
                session.add(row)
            await session.flush()
            return [self._adjustment_dict(row) for row in rows]

    async def list_adjustments(
        self, discord_user_id: str, *, cycle_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        filters = [ScoreAdjustmentRow.discord_user_id == discord_user_id]
        if cycle_id:
            filters.append(ScoreAdjustmentRow.cycle_id == cycle_id)
        statement = (
            select(ScoreAdjustmentRow)
            .where(*filters)
            .order_by(ScoreAdjustmentRow.created_at.desc())
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._adjustment_dict(row) for row in rows]

    async def find_member(self, query: str) -> LinkedUser | None:
        """Resolve a Discord id, exact Discord handle, or exact X handle to a member."""
        value = query.strip().lstrip("@")
        if not value:
            return None
        async with self.sessions() as session:
            if value.isdigit():
                row = await session.get(UserRow, value)
                if row is not None:
                    return self._linked_user(row)
            row = await session.scalar(
                select(UserRow).where(func.lower(UserRow.discord_username) == value.lower())
            )
            if row is None:
                row = await session.scalar(
                    select(UserRow).where(func.lower(UserRow.twitter_handle) == value.lower())
                )
        return self._linked_user(row) if row else None

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
                        twitter_handle=user.twitter_handle or "",
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
            started = await session.get(ConfigRow, "cycle_started_at")
            month_start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_runs = (
                await session.scalars(
                    select(ScanRunRow).where(
                        ScanRunRow.started_at >= month_start, ScanRunRow.status == "complete"
                    )
                )
            ).all()
            # twitterapi.io bills 15 credits per item returned and 10 per profile checked.
            credits_this_month = 0
            for run in month_runs:
                summary = run.summary or {}
                items = int(summary.get("tweets_returned") or 0)
                requests = int(summary.get("api_requests") or 0)
                checked = int(summary.get("x_checked") or 0)
                credits_this_month += max(items, requests) * 15 + checked * 10
            last_scan = await session.scalar(
                select(ScanRunRow).order_by(ScanRunRow.started_at.desc()).limit(1)
            )
            names = await self._member_names(session, {last_scan.triggered_by}) if last_scan else {}
        return {
            "linked_members": linked_count,
            "total_score": round(float(total_score or 0), 2),
            "active_actions": action_count,
            "tracked_posts": tracked_count,
            "cycle_id": cycle.value if cycle else "",
            "cycle_started_at": started.value if started else "",
            "credits_this_month": credits_this_month,
            "scans_this_month": len(month_runs),
            "last_scan": self._scan_dict(last_scan, names) if last_scan else None,
        }

    @staticmethod
    async def _overview_counts(session: AsyncSession, cycle_id: str) -> tuple[int, float, int, int]:
        linked_count = int(
            await session.scalar(
                select(func.count())
                .select_from(UserRow)
                .where(UserRow.active.is_(True), UserRow.twitter_user_id.is_not(None))
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
        protected: bool | None = None,
        linked: bool | None = None,
        x_ok: bool | None = None,
        points: str = "any",
        low_threshold: float | None = None,
        sort: str = "score_desc",
        joined: str = "any",
        grace_days: int = 30,
        min_score: float | None = None,
        max_score: float | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Filter and sort the registry.

        ``points`` is one of any / positive / zero / low (at or below ``low_threshold``).
        ``sort`` is one of score_desc, score_asc, name, last_signal, linked_at.
        """
        filters = []
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    UserRow.discord_username.ilike(pattern),
                    UserRow.twitter_handle.ilike(pattern),
                    UserRow.discord_user_id.ilike(pattern),
                    UserRow.special_role_names.ilike(pattern),
                )
            )
        if active is not None:
            filters.append(UserRow.active.is_(active))
        if protected is not None:
            filters.append(UserRow.special_role.is_(protected))
        if linked is not None:
            has_x = UserRow.twitter_user_id.is_not(None)
            filters.append(has_x if linked else UserRow.twitter_user_id.is_(None))
        if x_ok is not None:
            healthy = UserRow.x_status.in_(["", "ok"])
            filters.append(healthy if x_ok else ~healthy)
        if points == "positive":
            filters.append(UserRow.score > 0)
        elif points == "zero":
            filters.append(UserRow.score <= 0)
        elif points == "low" and low_threshold is not None:
            filters.append(UserRow.score <= low_threshold)
        if min_score is not None:
            filters.append(UserRow.score >= min_score)
        if max_score is not None:
            filters.append(UserRow.score <= max_score)
        if joined in {"new", "established"} and grace_days > 0:
            cutoff = utc_now() - timedelta(days=grace_days)
            if joined == "new":
                filters.append(UserRow.discord_joined_at > cutoff)
            else:
                filters.append(
                    or_(UserRow.discord_joined_at.is_(None), UserRow.discord_joined_at <= cutoff)
                )
        orders = {
            "score_desc": (UserRow.score.desc(), UserRow.discord_username),
            "score_asc": (UserRow.score.asc(), UserRow.discord_username),
            "name": (func.lower(UserRow.discord_username),),
            "last_signal": (UserRow.last_active_at.desc().nulls_last(), UserRow.discord_username),
            "linked_at": (UserRow.linked_at.desc(), UserRow.discord_username),
            "joined": (UserRow.discord_joined_at.desc().nulls_last(), UserRow.discord_username),
        }
        count_statement = select(func.count()).select_from(UserRow).where(*filters)
        statement = (
            select(UserRow)
            .where(*filters)
            .order_by(*orders.get(sort, orders["score_desc"]))
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
            if active and not row.active and row.twitter_user_id:
                duplicate = await session.scalar(
                    select(UserRow).where(
                        UserRow.active.is_(True),
                        UserRow.discord_user_id != discord_user_id,
                        or_(
                            UserRow.twitter_user_id == row.twitter_user_id,
                            func.lower(UserRow.twitter_handle)
                            == (row.twitter_handle or "").lower(),
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
        discord_user_id: str = "",
        sort: str = "occurred_desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        filters = []
        if action_type:
            filters.append(ActionRow.action_type == action_type)
        if discord_user_id:
            filters.append(ActionRow.discord_user_id == discord_user_id)
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
                .order_by(ACTION_ORDERS.get(sort, ACTION_ORDERS["occurred_desc"]))
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.scalars(statement)).all()
            # Every other screen leads with the Discord name; this one used to lead with the
            # X handle, so the same person read as two different people across the app.
            names = await self._member_names(session, {row.discord_user_id for row in rows})
        return {
            "items": [
                {
                    **asdict(self._action_from_row(row)),
                    "discord_username": names.get(row.discord_user_id, ""),
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "sort": sort if sort in ACTION_ORDERS else "occurred_desc",
        }

    async def paginated_audit(
        self, *, event_type: str = "", search: str = "", page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        filters = [AuditRow.event_type == event_type] if event_type else []
        if search.strip():
            pattern = f"%{search.strip()}%"
            member_ids = select(UserRow.discord_user_id).where(
                UserRow.discord_username.ilike(pattern)
            )
            filters.append(
                or_(
                    AuditRow.actor_discord_id.ilike(pattern),
                    AuditRow.subject_discord_id.ilike(pattern),
                    AuditRow.old_value.ilike(pattern),
                    AuditRow.new_value.ilike(pattern),
                    AuditRow.event_type.ilike(pattern),
                    AuditRow.actor_discord_id.in_(member_ids),
                    AuditRow.subject_discord_id.in_(member_ids),
                )
            )
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
            ids = {row.actor_discord_id for row in rows} | {row.subject_discord_id for row in rows}
            ids.discard("")
            names: dict[str, str] = {}
            if ids:
                for user_id, username in (
                    await session.execute(
                        select(UserRow.discord_user_id, UserRow.discord_username).where(
                            UserRow.discord_user_id.in_(ids)
                        )
                    )
                ).all():
                    names[user_id] = username
            event_types = sorted(
                value
                for (value,) in (
                    await session.execute(select(AuditRow.event_type).distinct())
                ).all()
                if value
            )
        return {
            "event_types": event_types,
            "items": [
                {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "actor_discord_id": row.actor_discord_id,
                    "actor_name": names.get(row.actor_discord_id, ""),
                    "subject_discord_id": row.subject_discord_id,
                    "subject_name": names.get(row.subject_discord_id, ""),
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
    def _scan_dict(row: ScanRunRow, names: dict[str, str] | None = None) -> dict[str, Any]:
        return {
            "scan_id": row.scan_id,
            "period": row.period,
            "status": row.status,
            "triggered_by": row.triggered_by,
            "triggered_by_name": (names or {}).get(row.triggered_by, ""),
            "source": row.source,
            "started_at": isoformat(row.started_at),
            "completed_at": isoformat(row.completed_at) if row.completed_at else "",
            "summary": row.summary,
            "error": row.error,
        }

    async def fail_stale_scans(self, reason: str) -> int:
        """Mark scans still flagged as running as failed; used after a restart.

        A scan only lives in memory while it runs, so a deploy or crash mid-scan would
        otherwise leave its report stuck on "running" forever.
        """
        async with self.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(ScanRunRow).where(ScanRunRow.status == "running").with_for_update()
                )
            ).all()
            now = utc_now()
            for row in rows:
                row.status = "failed"
                row.completed_at = now
                row.error = reason[:4000]
            return len(rows)

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

    async def actions_for_tweet(self, tweet_id: str) -> list[EngagementAction]:
        """Every logged action (any cycle) whose own tweet id or source post is ``tweet_id``."""
        statement = (
            select(ActionRow)
            .where(or_(ActionRow.action_tweet_id == tweet_id, ActionRow.source_post_id == tweet_id))
            .order_by(ActionRow.occurred_at.desc())
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._action_from_row(row) for row in rows]

    async def list_snapshots(self) -> list[dict[str, Any]]:
        """Every leaderboard reset, newest first, with the full standings it froze."""
        statement = select(HistoricalSnapshotRow).order_by(
            HistoricalSnapshotRow.reset_at.desc(), HistoricalSnapshotRow.rank
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(
                row.snapshot_id,
                {
                    "snapshot_id": row.snapshot_id,
                    "cycle_id": row.cycle_id,
                    "reset_at": isoformat(row.reset_at),
                    "reset_by_discord_id": row.reset_by_discord_id,
                    "members": [],
                },
            )
            entry["members"].append(
                {
                    "rank": row.rank,
                    "discord_user_id": row.discord_user_id,
                    "discord_username": row.discord_username,
                    "twitter_handle": row.twitter_handle,
                    "score": float(row.score or 0),
                }
            )
        return list(grouped.values())

    async def paginated_scans(self, *, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        count_statement = select(func.count()).select_from(ScanRunRow)
        statement = (
            select(ScanRunRow)
            .order_by(ScanRunRow.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        async with self.sessions() as session:
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.scalars(statement)).all()
            names = await self._member_names(session, {row.triggered_by for row in rows})
        return {
            "items": [self._scan_dict(row, names) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def recent_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ScanRunRow).order_by(ScanRunRow.started_at.desc()).limit(limit)
                )
            ).all()
            names = await self._member_names(session, {row.triggered_by for row in rows})
        return [self._scan_dict(row, names) for row in rows]

    @staticmethod
    async def _member_names(session: AsyncSession, ids: set[str]) -> dict[str, str]:
        """Discord handles for the given ids, so reports can name people instead of numbers."""
        wanted = {value for value in ids if value}
        if not wanted:
            return {}
        rows = (
            await session.execute(
                select(UserRow.discord_user_id, UserRow.discord_username).where(
                    UserRow.discord_user_id.in_(wanted)
                )
            )
        ).all()
        return {user_id: username for user_id, username in rows}

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
