from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from majors_lair_bot.twitter_client import parse_tweet, parse_twitter_user
from majors_lair_bot.utils import (
    normalize_handle,
    normalize_text,
    parse_period,
    parse_status_url,
)


def test_handle_and_status_url_normalization() -> None:
    assert normalize_handle(" @M_M3L ") == "m_m3l"
    assert parse_status_url("https://x.com/m_m3l/status/123456?s=20") == "123456"
    with pytest.raises(ValueError):
        normalize_handle("bad handle")
    with pytest.raises(ValueError):
        parse_status_url("https://example.com/123")


def test_period_validation() -> None:
    assert parse_period("week") == (timedelta(days=7), "7d")
    assert parse_period("24h") == (timedelta(hours=24), "24h")
    with pytest.raises(ValueError):
        parse_period("90d")


def test_text_normalization_removes_links_mentions_and_emoji() -> None:
    assert normalize_text("@m_m3l GM 🔥 https://example.com") == "gm"


def test_parse_twitterapi_io_tweet_shape() -> None:
    tweet = parse_tweet(
        {
            "id": "123",
            "text": "Useful reply",
            "createdAt": "2026-08-20T10:00:00Z",
            "author": {"id": "42", "userName": "LinkedUser"},
            "inReplyToId": "99",
            "extendedEntities": {"media": [{"type": "photo"}]},
        }
    )
    assert tweet.tweet_id == "123"
    assert tweet.author_id == "42"
    assert tweet.author_handle == "linkeduser"
    assert tweet.created_at == datetime(2026, 8, 20, 10, tzinfo=UTC)
    assert tweet.is_reply is True
    assert tweet.has_media is True


def test_parse_twitter_user_shape() -> None:
    assert parse_twitter_user({"id": "42", "userName": "Alice", "name": "Alice A"}) == (
        "42",
        "alice",
        "Alice A",
    )
