from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gspread
from gspread.exceptions import WorksheetNotFound
from gspread.utils import rowcol_to_a1

from .models import ActionType, EngagementAction, LinkedUser, ReconcileScope
from .scoring import CONFIG_DESCRIPTIONS, DEFAULT_CONFIG
from .utils import isoformat, parse_bool, parse_datetime

LOGGER = logging.getLogger(__name__)

TAB_HEADERS: dict[str, list[str]] = {
    "Users": [
        "discord_user_id",
        "discord_username",
        "twitter_handle",
        "twitter_user_id",
        "linked_at",
        "updated_at",
        "active",
        "score",
        "last_active_at",
        "handle_history",
    ],
    "ActionsLog": [
        "action_key",
        "cycle_id",
        "discord_user_id",
        "twitter_user_id",
        "twitter_handle",
        "action_type",
        "target_handle",
        "source_post_id",
        "action_tweet_id",
        "action_url",
        "text",
        "normalized_text",
        "content_hash",
        "has_media",
        "occurred_at",
        "points",
        "reason",
        "active",
        "first_seen_at",
        "last_seen_at",
    ],
    "AuditLog": [
        "event_id",
        "event_type",
        "actor_discord_id",
        "subject_discord_id",
        "old_value",
        "new_value",
        "details",
        "created_at",
    ],
    "TrackedPosts": [
        "tweet_id",
        "url",
        "source_handle",
        "discovered_at",
        "origin",
        "active",
        "last_checked_at",
        "post_created_at",
    ],
    "Config": ["key", "value", "description"],
    "HistoricalSnapshots": [
        "snapshot_id",
        "cycle_id",
        "discord_user_id",
        "discord_username",
        "twitter_handle",
        "score",
        "rank",
        "reset_by_discord_id",
        "reset_at",
    ],
}


class SheetRepositoryError(RuntimeError):
    pass


class LinkConflictError(SheetRepositoryError):
    pass


class GoogleSheetRepository:
    def __init__(
        self,
        *,
        sheet_id: str,
        credentials_file: Path | None,
        credentials_info: dict[str, object] | None,
    ) -> None:
        self._sheet_id = sheet_id
        self._credentials_file = credentials_file
        self._credentials_info = credentials_info
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._lock = asyncio.Lock()

    def _connect(self) -> gspread.Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet
        if self._credentials_info is not None:
            client = gspread.service_account_from_dict(dict(self._credentials_info))
        elif self._credentials_file is not None:
            client = gspread.service_account(filename=str(self._credentials_file))
        else:
            raise SheetRepositoryError("Google service-account credentials are not configured")
        self._spreadsheet = client.open_by_key(self._sheet_id)
        return self._spreadsheet

    def _worksheet(self, title: str) -> gspread.Worksheet:
        spreadsheet = self._connect()
        try:
            return spreadsheet.worksheet(title)
        except WorksheetNotFound:
            return spreadsheet.add_worksheet(title=title, rows=1000, cols=30)

    def _read_table(self, title: str) -> tuple[list[str], list[dict[str, Any]]]:
        worksheet = self._worksheet(title)
        values = worksheet.get_all_values()
        if not values:
            return [], []
        headers = [str(item).strip() for item in values[0]]
        while headers and not headers[-1]:
            headers.pop()
        rows: list[dict[str, Any]] = []
        for raw in values[1:]:
            if not any(str(cell).strip() for cell in raw):
                continue
            padded = [*raw, *([""] * max(0, len(headers) - len(raw)))]
            rows.append(dict(zip(headers, padded, strict=False)))
        return headers, rows

    def _write_table(self, title: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
        worksheet = self._worksheet(title)
        values = [headers]
        values.extend([[row.get(header, "") for header in headers] for row in rows])
        worksheet.clear()
        worksheet.update(range_name="A1", values=values, value_input_option="RAW")

    def _write_users(self, headers: list[str], rows: list[dict[str, Any]]) -> None:
        """Update only bot-owned Users columns, preserving unrelated formulas and formatting."""
        worksheet = self._worksheet("Users")
        complete_headers = [*headers]
        for header in TAB_HEADERS["Users"]:
            if header not in complete_headers:
                complete_headers.append(header)
        if complete_headers != headers:
            worksheet.update(range_name="A1", values=[complete_headers], value_input_option="RAW")
        if not rows:
            return
        updates = []
        for header in TAB_HEADERS["Users"]:
            column = complete_headers.index(header) + 1
            column_name = rowcol_to_a1(1, column)[:-1]
            updates.append(
                {
                    "range": f"{column_name}2:{column_name}{len(rows) + 1}",
                    "values": [[row.get(header, "")] for row in rows],
                }
            )
        worksheet.batch_update(updates, value_input_option="RAW")

    def _ensure_schema_sync(self) -> None:
        for title, required_headers in TAB_HEADERS.items():
            worksheet = self._worksheet(title)
            existing = [item.strip() for item in worksheet.row_values(1)]
            headers = [*existing]
            for header in required_headers:
                if header not in headers:
                    headers.append(header)
            if headers != existing:
                worksheet.update(range_name="A1", values=[headers], value_input_option="RAW")
        self._ensure_config_defaults_sync()

    def _ensure_config_defaults_sync(self) -> None:
        headers, rows = self._read_table("Config")
        headers = headers or TAB_HEADERS["Config"]
        known = {str(row.get("key", "")): row for row in rows}
        changed = False
        for key, value in DEFAULT_CONFIG.items():
            if key not in known:
                rows.append(
                    {
                        "key": key,
                        "value": value,
                        "description": CONFIG_DESCRIPTIONS.get(
                            key, "Editable scoring or scan configuration."
                        ),
                    }
                )
                changed = True
        if changed:
            self._write_table("Config", headers, rows)

    async def ensure_schema(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._ensure_schema_sync)

    def _get_config_sync(self) -> dict[str, str]:
        _, rows = self._read_table("Config")
        return {
            str(row.get("key", "")).strip(): str(row.get("value", "")).strip()
            for row in rows
            if str(row.get("key", "")).strip()
        }

    async def get_config(self) -> dict[str, str]:
        async with self._lock:
            return await asyncio.to_thread(self._get_config_sync)

    def _set_config_values_sync(self, changes: dict[str, str]) -> None:
        headers, rows = self._read_table("Config")
        headers = headers or TAB_HEADERS["Config"]
        indexed = {str(row.get("key", "")): row for row in rows}
        for key, value in changes.items():
            if key in indexed:
                indexed[key]["value"] = value
            else:
                rows.append(
                    {
                        "key": key,
                        "value": value,
                        "description": CONFIG_DESCRIPTIONS.get(key, "Runtime configuration."),
                    }
                )
        self._write_table("Config", headers, rows)

    async def set_config_values(self, changes: dict[str, str]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_config_values_sync, changes)

    @staticmethod
    def _linked_user(row: dict[str, Any]) -> LinkedUser:
        try:
            score = float(row.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        return LinkedUser(
            discord_user_id=str(row.get("discord_user_id", "")),
            discord_username=str(row.get("discord_username", "")),
            twitter_handle=str(row.get("twitter_handle", "")).removeprefix("@").lower(),
            twitter_user_id=str(row.get("twitter_user_id", "")),
            linked_at=str(row.get("linked_at", "")),
            updated_at=str(row.get("updated_at", "")),
            active=parse_bool(row.get("active")),
            score=score,
            last_active_at=str(row.get("last_active_at", "")),
            handle_history=str(row.get("handle_history", "")),
        )

    def _list_users_sync(self, active_only: bool) -> list[LinkedUser]:
        _, rows = self._read_table("Users")
        users = [self._linked_user(row) for row in rows if row.get("discord_user_id")]
        return [user for user in users if user.active] if active_only else users

    async def list_users(self, *, active_only: bool = False) -> list[LinkedUser]:
        async with self._lock:
            return await asyncio.to_thread(self._list_users_sync, active_only)

    def _link_user_sync(
        self,
        discord_user_id: str,
        discord_username: str,
        twitter_handle: str,
        twitter_user_id: str,
    ) -> tuple[str, str]:
        headers, rows = self._read_table("Users")
        headers = headers or TAB_HEADERS["Users"]
        handle = twitter_handle.removeprefix("@").lower()
        for row in rows:
            if not parse_bool(row.get("active")):
                continue
            same_discord = str(row.get("discord_user_id", "")) == discord_user_id
            same_x_id = (
                bool(twitter_user_id) and str(row.get("twitter_user_id", "")) == twitter_user_id
            )
            same_handle = str(row.get("twitter_handle", "")).removeprefix("@").lower() == handle
            if not same_discord and (same_x_id or same_handle):
                raise LinkConflictError(
                    "That X account is already linked to another Discord member"
                )

        now = isoformat()
        old_handle = ""
        target = next(
            (row for row in rows if str(row.get("discord_user_id", "")) == discord_user_id),
            None,
        )
        if target is None:
            target = {header: "" for header in headers}
            rows.append(target)
            target["linked_at"] = now
        else:
            old_handle = str(target.get("twitter_handle", "")).removeprefix("@").lower()
            if old_handle and old_handle != handle:
                history = [
                    item for item in str(target.get("handle_history", "")).split("|") if item
                ]
                if old_handle not in history:
                    history.append(old_handle)
                target["handle_history"] = "|".join(history)

        target.update(
            {
                "discord_user_id": discord_user_id,
                "discord_username": discord_username,
                "twitter_handle": handle,
                "twitter_user_id": twitter_user_id,
                "updated_at": now,
                "active": "TRUE",
                "score": target.get("score", 0) or 0,
            }
        )
        self._write_users(headers, rows)
        return old_handle, handle

    async def link_user(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        twitter_handle: str,
        twitter_user_id: str,
    ) -> tuple[str, str]:
        async with self._lock:
            return await asyncio.to_thread(
                self._link_user_sync,
                discord_user_id,
                discord_username,
                twitter_handle,
                twitter_user_id,
            )

    def _unlink_user_sync(self, discord_user_id: str) -> str:
        headers, rows = self._read_table("Users")
        for row in rows:
            if str(row.get("discord_user_id", "")) != discord_user_id:
                continue
            if not parse_bool(row.get("active")):
                return ""
            old_handle = str(row.get("twitter_handle", ""))
            row["active"] = "FALSE"
            row["updated_at"] = isoformat()
            self._write_users(headers, rows)
            return old_handle
        return ""

    async def unlink_user(self, discord_user_id: str) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._unlink_user_sync, discord_user_id)

    async def get_user(self, discord_user_id: str) -> LinkedUser | None:
        users = await self.list_users(active_only=False)
        return next((user for user in users if user.discord_user_id == discord_user_id), None)

    async def leaderboard(self, limit: int = 25) -> list[LinkedUser]:
        users = await self.list_users(active_only=True)
        return sorted(users, key=lambda user: (-user.score, user.discord_username.lower()))[:limit]

    async def low_activity(self, threshold: float) -> list[LinkedUser]:
        users = await self.list_users(active_only=True)
        return sorted(
            (user for user in users if user.score <= threshold),
            key=lambda user: (user.score, user.discord_username.lower()),
        )

    def _append_audit_sync(
        self,
        event_type: str,
        actor_discord_id: str,
        subject_discord_id: str,
        old_value: str,
        new_value: str,
        details: dict[str, Any] | str,
    ) -> str:
        event_id = uuid.uuid4().hex
        value = details if isinstance(details, str) else json.dumps(details, separators=(",", ":"))
        row = [
            event_id,
            event_type,
            actor_discord_id,
            subject_discord_id,
            old_value,
            new_value,
            value,
            isoformat(),
        ]
        self._worksheet("AuditLog").append_row(row, value_input_option="RAW")
        return event_id

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
        async with self._lock:
            return await asyncio.to_thread(
                self._append_audit_sync,
                event_type,
                actor_discord_id,
                subject_discord_id,
                old_value,
                new_value,
                details,
            )

    def _upsert_tracked_posts_sync(self, posts: list[dict[str, Any]]) -> None:
        headers, rows = self._read_table("TrackedPosts")
        headers = headers or TAB_HEADERS["TrackedPosts"]
        indexed = {str(row.get("tweet_id", "")): row for row in rows}
        for post in posts:
            tweet_id = str(post["tweet_id"])
            if tweet_id in indexed:
                indexed[tweet_id].update(post)
            else:
                row = {header: "" for header in headers}
                row.update(post)
                rows.append(row)
                indexed[tweet_id] = row
        self._write_table("TrackedPosts", headers, rows)

    async def upsert_tracked_posts(self, posts: list[dict[str, Any]]) -> None:
        if not posts:
            return
        async with self._lock:
            await asyncio.to_thread(self._upsert_tracked_posts_sync, posts)

    def _list_tracked_posts_sync(self, active_only: bool) -> list[dict[str, Any]]:
        _, rows = self._read_table("TrackedPosts")
        if active_only:
            rows = [row for row in rows if parse_bool(row.get("active"), default=True)]
        return rows

    async def list_tracked_posts(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_tracked_posts_sync, active_only)

    @staticmethod
    def _action_from_row(row: dict[str, Any]) -> EngagementAction:
        try:
            points = float(row.get("points", 0) or 0)
        except (TypeError, ValueError):
            points = 0.0
        return EngagementAction(
            action_key=str(row.get("action_key", "")),
            cycle_id=str(row.get("cycle_id", "")),
            discord_user_id=str(row.get("discord_user_id", "")),
            twitter_user_id=str(row.get("twitter_user_id", "")),
            twitter_handle=str(row.get("twitter_handle", "")),
            action_type=ActionType(str(row.get("action_type", "mention"))),
            target_handle=str(row.get("target_handle", "")),
            source_post_id=str(row.get("source_post_id", "")),
            action_tweet_id=str(row.get("action_tweet_id", "")),
            action_url=str(row.get("action_url", "")),
            text=str(row.get("text", "")),
            normalized_text=str(row.get("normalized_text", "")),
            content_hash=str(row.get("content_hash", "")),
            has_media=parse_bool(row.get("has_media")),
            occurred_at=str(row.get("occurred_at", "")),
            points=points,
            reason=str(row.get("reason", "")),
            active=parse_bool(row.get("active"), default=True),
            first_seen_at=str(row.get("first_seen_at", "")),
            last_seen_at=str(row.get("last_seen_at", "")),
        )

    @staticmethod
    def _action_to_row(action: EngagementAction) -> dict[str, Any]:
        row = asdict(action)
        row["action_type"] = action.action_type.value
        row["has_media"] = "TRUE" if action.has_media else "FALSE"
        row["active"] = "TRUE" if action.active else "FALSE"
        return row

    def _list_actions_sync(
        self, cycle_id: str | None, include_inactive: bool
    ) -> list[EngagementAction]:
        _, rows = self._read_table("ActionsLog")
        actions = []
        for row in rows:
            if not row.get("action_key"):
                continue
            if cycle_id is not None and str(row.get("cycle_id", "")) != cycle_id:
                continue
            try:
                action = self._action_from_row(row)
            except ValueError:
                LOGGER.warning("Skipping ActionsLog row with invalid action_type: %s", row)
                continue
            if include_inactive or action.active:
                actions.append(action)
        return actions

    async def list_actions(
        self, *, cycle_id: str | None = None, include_inactive: bool = True
    ) -> list[EngagementAction]:
        async with self._lock:
            return await asyncio.to_thread(self._list_actions_sync, cycle_id, include_inactive)

    @staticmethod
    def _scope_matches(action: EngagementAction, scope: ReconcileScope) -> bool:
        if action.action_type != scope.action_type:
            return False
        if action.target_handle.lower() != scope.target_handle.lower():
            return False
        if scope.source_post_id and action.source_post_id != scope.source_post_id:
            return False
        if scope.since_iso and parse_datetime(action.occurred_at) < parse_datetime(scope.since_iso):
            return False
        return not (
            scope.until_iso and parse_datetime(action.occurred_at) > parse_datetime(scope.until_iso)
        )

    def _reconcile_actions_sync(
        self,
        cycle_id: str,
        discovered: list[EngagementAction],
        scopes: list[ReconcileScope],
    ) -> int:
        headers, rows = self._read_table("ActionsLog")
        headers = headers or TAB_HEADERS["ActionsLog"]
        existing: dict[str, EngagementAction] = {}
        passthrough: list[dict[str, Any]] = []
        for row in rows:
            try:
                action = self._action_from_row(row)
            except ValueError:
                passthrough.append(row)
                continue
            if action.action_key:
                existing[action.action_key] = action

        now = isoformat()
        discovered_keys = {action.action_key for action in discovered}
        changed = 0
        complete_scopes = [scope for scope in scopes if scope.complete]
        for action in existing.values():
            if action.cycle_id != cycle_id or not action.active:
                continue
            if action.action_key in discovered_keys:
                continue
            if any(self._scope_matches(action, scope) for scope in complete_scopes):
                action.active = False
                action.last_seen_at = now
                changed += 1

        for candidate in discovered:
            previous = existing.get(candidate.action_key)
            if previous is not None:
                candidate.first_seen_at = previous.first_seen_at or now
                if candidate.action_type == ActionType.RETWEET:
                    candidate.occurred_at = previous.occurred_at or candidate.occurred_at
                if not previous.active:
                    changed += 1
            else:
                candidate.first_seen_at = now
                changed += 1
            candidate.last_seen_at = now
            candidate.active = True
            existing[candidate.action_key] = candidate

        ordered = sorted(
            existing.values(),
            key=lambda action: (action.cycle_id, action.occurred_at, action.action_key),
        )
        output = [*passthrough, *(self._action_to_row(action) for action in ordered)]
        self._write_table("ActionsLog", headers, output)
        return changed

    async def reconcile_actions(
        self,
        *,
        cycle_id: str,
        discovered: list[EngagementAction],
        scopes: list[ReconcileScope],
    ) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                self._reconcile_actions_sync, cycle_id, discovered, scopes
            )

    def _save_scored_actions_sync(
        self, cycle_id: str, scored_actions: list[EngagementAction]
    ) -> None:
        action_headers, action_rows = self._read_table("ActionsLog")
        action_headers = action_headers or TAB_HEADERS["ActionsLog"]
        scored_index = {action.action_key: action for action in scored_actions}
        output_actions: list[dict[str, Any]] = []
        for row in action_rows:
            key = str(row.get("action_key", ""))
            if key in scored_index:
                output_actions.append(self._action_to_row(scored_index.pop(key)))
            else:
                output_actions.append(row)
        output_actions.extend(self._action_to_row(action) for action in scored_index.values())
        self._write_table("ActionsLog", action_headers, output_actions)

        totals: dict[str, float] = {}
        latest: dict[str, str] = {}
        for action in scored_actions:
            if action.cycle_id != cycle_id or not action.active:
                continue
            totals[action.discord_user_id] = totals.get(action.discord_user_id, 0.0) + action.points
            if action.points > 0 and action.occurred_at > latest.get(action.discord_user_id, ""):
                latest[action.discord_user_id] = action.occurred_at

        user_headers, user_rows = self._read_table("Users")
        user_headers = user_headers or TAB_HEADERS["Users"]
        for row in user_rows:
            discord_id = str(row.get("discord_user_id", ""))
            if not discord_id:
                continue
            row["score"] = round(totals.get(discord_id, 0.0), 2)
            row["last_active_at"] = latest.get(discord_id, "")
        self._write_users(user_headers, user_rows)

    async def save_scored_actions(
        self, *, cycle_id: str, scored_actions: list[EngagementAction]
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_scored_actions_sync, cycle_id, scored_actions)

    async def user_history(
        self, discord_user_id: str, cycle_id: str, limit: int = 10
    ) -> list[EngagementAction]:
        actions = await self.list_actions(cycle_id=cycle_id, include_inactive=True)
        matches = [action for action in actions if action.discord_user_id == discord_user_id]
        return sorted(matches, key=lambda action: action.occurred_at, reverse=True)[:limit]

    def _reset_leaderboard_sync(self, actor_discord_id: str) -> tuple[str, str, int]:
        config = self._get_config_sync()
        old_cycle = config.get("current_cycle_id", "cycle_initial")
        reset_at = isoformat()
        new_cycle = f"cycle_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

        user_headers, user_rows = self._read_table("Users")
        active_rows = [row for row in user_rows if parse_bool(row.get("active"))]
        active_rows.sort(key=lambda row: -float(row.get("score", 0) or 0))
        snapshot_id = uuid.uuid4().hex
        snapshot_rows = []
        for rank, row in enumerate(active_rows, start=1):
            snapshot_rows.append(
                [
                    snapshot_id,
                    old_cycle,
                    str(row.get("discord_user_id", "")),
                    str(row.get("discord_username", "")),
                    str(row.get("twitter_handle", "")),
                    float(row.get("score", 0) or 0),
                    rank,
                    actor_discord_id,
                    reset_at,
                ]
            )
        if snapshot_rows:
            self._worksheet("HistoricalSnapshots").append_rows(
                snapshot_rows, value_input_option="RAW"
            )
        for row in user_rows:
            if row.get("discord_user_id"):
                row["score"] = 0
                row["last_active_at"] = ""
        self._write_users(user_headers or TAB_HEADERS["Users"], user_rows)
        self._set_config_values_sync({"current_cycle_id": new_cycle, "cycle_started_at": reset_at})
        return old_cycle, new_cycle, len(snapshot_rows)

    async def reset_leaderboard(self, actor_discord_id: str) -> tuple[str, str, int]:
        async with self._lock:
            return await asyncio.to_thread(self._reset_leaderboard_sync, actor_discord_id)
