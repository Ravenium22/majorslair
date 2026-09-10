from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import yarl

from majors_lair_bot.twitter_client import TwitterApiClient


class StubTwitterClient(TwitterApiClient):
    def __init__(self) -> None:
        super().__init__("not-a-real-key")
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _request_json(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((path, params or {}))
        if path == "/twitter/user/info":
            return {"data": {"id": "1", "userName": "m_m3l"}}
        return {"tweets": [], "users": [], "has_next_page": False}


class RepliesEnvelopeClient(TwitterApiClient):
    async def _request_json(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "replies": [
                {
                    "id": "reply-1",
                    "text": "A reply",
                    "createdAt": "2026-08-01T12:00:00Z",
                    "author": {"id": "user-1", "userName": "alice"},
                }
            ],
            "has_next_page": False,
        }


@pytest.mark.asyncio
async def test_exact_endpoint_parameter_names() -> None:
    client = StubTwitterClient()
    since = datetime(2026, 8, 1, tzinfo=UTC)
    until = datetime(2026, 8, 2, tzinfo=UTC)

    await client.get_user_info("m_m3l")
    await client.get_recent_tweets("m_m3l", since=since, until=until, max_pages=1)
    await client.get_replies("10", since=since, until=until, max_pages=1)
    await client.get_quotes("10", since=since, max_pages=1)
    await client.get_retweeters("10", max_pages=1)
    await client.get_mentions("m_m3l", since=since, until=until, max_pages=1)
    await client.get_tweets(["10", "11"])

    calls = dict(client.calls)
    assert calls["/twitter/user/info"] == {"userName": "m_m3l"}
    assert calls["/twitter/user/last_tweets"] == {
        "userName": "m_m3l",
        "includeReplies": "false",
    }
    for path, params in client.calls:
        for key, value in params.items():
            assert not isinstance(value, bool), f"{path} sends boolean {key}; yarl rejects it"
            assert isinstance(value, (str, int)), f"{path} sends unsupported {key}={value!r}"
    assert calls["/twitter/tweet/replies"]["tweetId"] == "10"
    assert calls["/twitter/tweet/replies"]["sinceTime"] == int(since.timestamp())
    assert calls["/twitter/tweet/replies"]["untilTime"] == int(until.timestamp())
    assert calls["/twitter/tweet/quotes"] == {"tweetId": "10"}
    assert calls["/twitter/tweet/retweeters"] == {"tweetId": "10"}
    assert calls["/twitter/user/mentions"]["userName"] == "m_m3l"
    assert calls["/twitter/tweets"] == {"tweet_ids": "10,11"}


def test_query_params_are_url_safe() -> None:
    cleaned = TwitterApiClient._query_params(
        {"includeReplies": False, "flag": True, "count": 5, "skip": None, "name": "x"}
    )
    assert cleaned == {"includeReplies": "false", "flag": "true", "count": "5", "name": "x"}
    # The real URL builder must accept the cleaned values.
    yarl.URL("https://example.test/path").with_query(cleaned)


@pytest.mark.asyncio
async def test_replies_accepts_documented_replies_envelope() -> None:
    client = RepliesEnvelopeClient("unused")
    since = datetime(2026, 8, 1, tzinfo=UTC)
    until = datetime(2026, 8, 2, tzinfo=UTC)
    result = await client.get_replies("10", since=since, until=until, max_pages=1)
    assert result.complete is True
    assert result.items[0]["id"] == "reply-1"
