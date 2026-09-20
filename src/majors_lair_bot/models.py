from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ActionType(StrEnum):
    REPLY = "reply"
    QUOTE = "quote"
    RETWEET = "retweet"
    MENTION = "mention"


@dataclass(slots=True)
class LinkedUser:
    discord_user_id: str
    discord_username: str
    twitter_handle: str
    twitter_user_id: str
    linked_at: str
    updated_at: str
    active: bool = True
    score: float = 0.0
    last_active_at: str = ""
    handle_history: str = ""
    special_role: bool = False
    special_role_names: str = ""
    x_status: str = ""
    x_checked_at: str = ""


@dataclass(slots=True)
class Tweet:
    tweet_id: str
    text: str
    author_id: str
    author_handle: str
    created_at: datetime
    has_media: bool = False
    is_reply: bool = False
    reply_to_tweet_id: str = ""
    quoted_tweet_id: str = ""
    is_retweet: bool = False
    reply_count: int = 0
    quote_count: int = 0
    retweet_count: int = 0

    @property
    def url(self) -> str:
        handle = self.author_handle or "i"
        return f"https://x.com/{handle}/status/{self.tweet_id}"


@dataclass(slots=True)
class EngagementAction:
    action_key: str
    cycle_id: str
    discord_user_id: str
    twitter_user_id: str
    twitter_handle: str
    action_type: ActionType
    target_handle: str
    source_post_id: str
    action_tweet_id: str
    action_url: str
    text: str
    normalized_text: str
    content_hash: str
    has_media: bool
    occurred_at: str
    points: float = 0.0
    reason: str = ""
    active: bool = True
    first_seen_at: str = ""
    last_seen_at: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileScope:
    action_type: ActionType
    target_handle: str
    source_post_id: str = ""
    since_iso: str = ""
    until_iso: str = ""
    complete: bool = False


@dataclass(slots=True)
class PageResult:
    items: list[dict[str, object]]
    complete: bool
    pages: int


@dataclass(slots=True)
class ScanSummary:
    period_label: str
    scan_id: str = ""
    source_posts: int = 0
    discovered: int = 0
    replies: int = 0
    quotes: int = 0
    retweets: int = 0
    mentions: int = 0
    skipped_unlinked: int = 0
    skipped_protected: int = 0
    swept_replies: int = 0
    incomplete_scopes: int = 0
    api_requests: int = 0
    tweets_returned: int = 0
    changed_actions: int = 0
    warnings: list[str] = field(default_factory=list)
    x_checked: int = 0
    x_unavailable: list[dict[str, str]] = field(default_factory=list)
    x_renamed: list[dict[str, str]] = field(default_factory=list)
    score_changes: list[dict[str, Any]] = field(default_factory=list)
    score_changes_total: int = 0
    # Every active member's points right after this scan, so each report carries a full
    # sheet even after later scans or a leaderboard reset.
    standings: list[dict[str, Any]] = field(default_factory=list)
