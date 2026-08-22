from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from .models import ActionType, EngagementAction
from .utils import normalize_text, parse_datetime, words

DEFAULT_CONFIG: dict[str, str] = {
    "current_cycle_id": "cycle_initial",
    "cycle_started_at": "",
    "primary_handle": "m_m3l",
    "secondary_handle": "majorslair",
    "default_check_period": "7d",
    "default_refresh_period": "24h",
    "max_source_pages": "5",
    "max_action_pages_per_post": "8",
    "max_mention_pages": "10",
    "low_activity_threshold": "5",
    "blacklist": "lfg,gm,gn,alpha,bullish,fire,moon,send it,lets go,let's go",
    "reference_keywords": (
        "onchain,on-chain,dyor,liquidity,volume,holders,tokenomics,roadmap,thesis,data,research"
    ),
    "reply_primary": "12",
    "quote_primary": "15",
    "retweet_primary": "7",
    "mention_primary": "10",
    "reply_secondary": "5",
    "quote_secondary": "7",
    "retweet_secondary": "3",
    "mention_secondary": "4",
    "minimum_words": "3",
    "low_effort_multiplier": "0.10",
    "word_bonus_8": "2",
    "word_bonus_20": "3",
    "question_bonus": "1",
    "reference_bonus": "2",
    "media_bonus": "2",
    "link_bonus": "1",
    "quality_bonus_cap": "8",
    "daily_scored_action_cap": "20",
}


CONFIG_DESCRIPTIONS: dict[str, str] = {
    "current_cycle_id": "Internal leaderboard cycle identifier; changed by reset.",
    "cycle_started_at": "UTC timestamp set by reset; scans never award older activity.",
    "primary_handle": "Primary X account, without @.",
    "secondary_handle": "Secondary X account, without @.",
    "default_check_period": "Default period for /check-engagement.",
    "default_refresh_period": "Period used by /refresh-engagement.",
    "max_source_pages": "Safety cap when fetching target account posts.",
    "max_action_pages_per_post": "Safety cap per replies/quotes/retweeters endpoint.",
    "max_mention_pages": "Safety cap per target account mention scan.",
    "low_activity_threshold": "Score at or below this appears in the low-activity report.",
    "blacklist": "Comma-separated low-effort words/phrases.",
    "reference_keywords": "Comma-separated research/on-chain value signals.",
    "minimum_words": "Fewer normalized words is treated as low effort.",
    "low_effort_multiplier": "Multiplier applied to non-retweet low-effort text.",
    "daily_scored_action_cap": "Per-user daily cap; later content remains logged at 0 points.",
}


@dataclass(frozen=True, slots=True)
class ScoringRules:
    primary_handle: str
    secondary_handle: str
    blacklist: frozenset[str]
    reference_keywords: frozenset[str]
    minimum_words: int
    low_effort_multiplier: float
    word_bonus_8: float
    word_bonus_20: float
    question_bonus: float
    reference_bonus: float
    media_bonus: float
    link_bonus: float
    quality_bonus_cap: float
    daily_scored_action_cap: int
    weights: Mapping[tuple[ActionType, str], float]

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> ScoringRules:
        merged = {**DEFAULT_CONFIG, **{key: str(value) for key, value in values.items()}}
        primary = merged["primary_handle"].strip().removeprefix("@").lower()
        secondary = merged["secondary_handle"].strip().removeprefix("@").lower()
        weights: dict[tuple[ActionType, str], float] = {}
        for action_type in ActionType:
            weights[(action_type, primary)] = float(merged[f"{action_type.value}_primary"])
            weights[(action_type, secondary)] = float(merged[f"{action_type.value}_secondary"])
        return cls(
            primary_handle=primary,
            secondary_handle=secondary,
            blacklist=frozenset(
                item.strip().lower() for item in merged["blacklist"].split(",") if item.strip()
            ),
            reference_keywords=frozenset(
                item.strip().lower()
                for item in merged["reference_keywords"].split(",")
                if item.strip()
            ),
            minimum_words=max(0, int(merged["minimum_words"])),
            low_effort_multiplier=max(0.0, min(1.0, float(merged["low_effort_multiplier"]))),
            word_bonus_8=float(merged["word_bonus_8"]),
            word_bonus_20=float(merged["word_bonus_20"]),
            question_bonus=float(merged["question_bonus"]),
            reference_bonus=float(merged["reference_bonus"]),
            media_bonus=float(merged["media_bonus"]),
            link_bonus=float(merged["link_bonus"]),
            quality_bonus_cap=float(merged["quality_bonus_cap"]),
            daily_scored_action_cap=max(1, int(merged["daily_scored_action_cap"])),
            weights=weights,
        )


def content_fingerprint(text: str) -> tuple[str, str]:
    normalized = normalize_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20] if normalized else ""
    return normalized, digest


class ScoringEngine:
    def __init__(self, rules: ScoringRules) -> None:
        self.rules = rules

    def score_all(self, actions: list[EngagementAction]) -> list[EngagementAction]:
        seen_content: dict[tuple[str, str], str] = {}
        daily_count: defaultdict[tuple[str, str], int] = defaultdict(int)
        ordered = sorted(
            actions,
            key=lambda item: (parse_datetime(item.occurred_at), item.action_key),
        )
        for action in ordered:
            if not action.active:
                action.points = 0.0
                action.reason = "Inactive or no longer public"
                continue

            day = parse_datetime(action.occurred_at).date().isoformat()
            day_key = (action.discord_user_id, day)
            content_key = (action.twitter_user_id or action.twitter_handle, action.content_hash)

            duplicate = bool(
                action.action_type != ActionType.RETWEET
                and action.content_hash
                and content_key in seen_content
                and seen_content[content_key] != action.action_key
            )
            if duplicate:
                action.points = 0.0
                action.reason = "Duplicate/repeated text"
                continue

            if daily_count[day_key] >= self.rules.daily_scored_action_cap:
                action.points = 0.0
                action.reason = (
                    f"Daily cap of {self.rules.daily_scored_action_cap} scored actions reached"
                )
                if action.content_hash:
                    seen_content[content_key] = action.action_key
                continue

            action.points, action.reason = self.score_one(action)
            if action.points > 0:
                daily_count[day_key] += 1
            if action.content_hash:
                seen_content[content_key] = action.action_key
        return actions

    def score_one(self, action: EngagementAction) -> tuple[float, str]:
        target = action.target_handle.lower().removeprefix("@")
        base = self.rules.weights.get((action.action_type, target), 0.0)
        if base <= 0:
            return 0.0, "No configured weight for this action/target"
        if action.action_type == ActionType.RETWEET:
            return round(base, 2), f"Base {action.action_type.value} on @{target}: {base:g}"

        normalized = action.normalized_text or normalize_text(action.text)
        token_list = words(normalized)
        token_set = set(token_list)
        blacklist_hit = normalized in self.rules.blacklist or (
            bool(token_set) and token_set.issubset(self.rules.blacklist)
        )
        if not normalized or blacklist_hit:
            return 0.0, "Blocked as empty/blacklisted low-effort text"

        reasons = [f"Base {action.action_type.value} on @{target}: {base:g}"]
        if len(token_list) < self.rules.minimum_words:
            value = round(base * self.rules.low_effort_multiplier, 2)
            reasons.append(
                f"low effort ({len(token_list)} words) ×{self.rules.low_effort_multiplier:g}"
            )
            return value, "; ".join(reasons)

        bonuses: list[tuple[str, float]] = []
        if len(token_list) >= 20:
            bonuses.append(("20+ words", self.rules.word_bonus_20))
        elif len(token_list) >= 8:
            bonuses.append(("8+ words", self.rules.word_bonus_8))
        if "?" in action.text:
            bonuses.append(("question", self.rules.question_bonus))
        if token_set.intersection(self.rules.reference_keywords):
            bonuses.append(("research/on-chain reference", self.rules.reference_bonus))
        if action.has_media:
            bonuses.append(("media", self.rules.media_bonus))
        if "http://" in action.text.lower() or "https://" in action.text.lower():
            bonuses.append(("supporting link", self.rules.link_bonus))

        raw_bonus = sum(value for _, value in bonuses)
        bonus = min(raw_bonus, self.rules.quality_bonus_cap)
        reasons.extend(f"{name} +{value:g}" for name, value in bonuses)
        if raw_bonus > bonus:
            reasons.append(f"quality bonus capped at +{bonus:g}")
        return round(base + bonus, 2), "; ".join(reasons)
