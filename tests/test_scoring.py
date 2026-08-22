from __future__ import annotations

from dataclasses import replace

import pytest

from majors_lair_bot.models import ActionType, EngagementAction
from majors_lair_bot.scoring import ScoringEngine, ScoringRules, content_fingerprint


def make_action(
    *,
    key: str = "a1",
    action_type: ActionType = ActionType.REPLY,
    target: str = "m_m3l",
    text: str = "This is a useful onchain thesis with supporting data, what do you think?",
    occurred_at: str = "2026-08-01T12:00:00Z",
) -> EngagementAction:
    normalized, fingerprint = content_fingerprint(text)
    return EngagementAction(
        action_key=key,
        cycle_id="cycle_test",
        discord_user_id="discord-1",
        twitter_user_id="twitter-1",
        twitter_handle="member",
        action_type=action_type,
        target_handle=target,
        source_post_id="source-1",
        action_tweet_id=key,
        action_url=f"https://x.com/member/status/{key}",
        text=text,
        normalized_text=normalized,
        content_hash=fingerprint,
        has_media=False,
        occurred_at=occurred_at,
    )


@pytest.fixture
def engine() -> ScoringEngine:
    return ScoringEngine(ScoringRules.from_mapping({}))


def test_thoughtful_primary_reply_gets_quality_bonuses(engine: ScoringEngine) -> None:
    points, reason = engine.score_one(make_action())
    assert points == 17
    assert "Base reply on @m_m3l: 12" in reason
    assert "8+ words +2" in reason
    assert "question +1" in reason
    assert "research/on-chain reference +2" in reason


@pytest.mark.parametrize("text", ["gm", "🔥", "@m_m3l lfg", "alpha bullish"])
def test_blacklisted_low_effort_text_gets_zero(engine: ScoringEngine, text: str) -> None:
    points, reason = engine.score_one(make_action(text=text))
    assert points == 0
    assert "blacklisted" in reason


def test_short_non_blacklisted_reply_gets_minimal_points(engine: ScoringEngine) -> None:
    points, reason = engine.score_one(make_action(text="Nice analysis"))
    assert points == 1.2
    assert "low effort" in reason


def test_secondary_retweet_uses_lower_weight(engine: ScoringEngine) -> None:
    points, reason = engine.score_one(
        make_action(action_type=ActionType.RETWEET, target="majorslair", text="")
    )
    assert points == 3
    assert "@majorslair" in reason


def test_repeated_text_is_only_scored_once(engine: ScoringEngine) -> None:
    first = make_action(key="a1")
    second = replace(first, action_key="a2", action_tweet_id="a2")
    scored = engine.score_all([second, first])
    by_key = {action.action_key: action for action in scored}
    assert by_key["a1"].points > 0
    assert by_key["a2"].points == 0
    assert by_key["a2"].reason == "Duplicate/repeated text"


def test_daily_cap_is_deterministic() -> None:
    rules = ScoringRules.from_mapping({"daily_scored_action_cap": "1"})
    engine = ScoringEngine(rules)
    first = make_action(key="a1", text="A detailed first comment about token liquidity data")
    second = make_action(
        key="a2",
        text="A separate thoughtful question about community growth plans?",
        occurred_at="2026-08-01T13:00:00Z",
    )
    engine.score_all([second, first])
    assert first.points > 0
    assert second.points == 0
    assert "Daily cap" in second.reason
