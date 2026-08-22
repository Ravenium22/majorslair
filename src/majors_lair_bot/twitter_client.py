from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp

from .models import PageResult, Tweet
from .utils import deep_get, parse_datetime

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://api.twitterapi.io"


class TwitterApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, path: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.path = path


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_twitter_user(record: dict[str, Any]) -> tuple[str, str, str]:
    user = _as_dict(record)
    nested = _as_dict(deep_get(user, (("author",), ("user",)), default={}))
    source = nested or user
    user_id = str(
        deep_get(
            source,
            (("id",), ("id_str",), ("userId",), ("rest_id",), ("legacy", "id_str")),
        )
    )
    handle = (
        str(
            deep_get(
                source,
                (
                    ("userName",),
                    ("username",),
                    ("screenName",),
                    ("screen_name",),
                    ("legacy", "screen_name"),
                ),
            )
        )
        .removeprefix("@")
        .lower()
    )
    name = str(deep_get(source, (("name",), ("displayName",), ("legacy", "name"))))
    return user_id, handle, name


def parse_tweet(record: dict[str, Any]) -> Tweet:
    raw = _as_dict(record)
    legacy = _as_dict(raw.get("legacy"))
    author = _as_dict(deep_get(raw, (("author",), ("user",)), default={}))
    author_id = str(
        deep_get(
            raw,
            (
                ("authorId",),
                ("author_id",),
                ("userId",),
                ("user_id",),
                ("legacy", "user_id_str"),
            ),
            default=deep_get(author, (("id",), ("id_str",), ("rest_id",))),
        )
    )
    author_handle = (
        str(
            deep_get(
                author,
                (
                    ("userName",),
                    ("username",),
                    ("screenName",),
                    ("screen_name",),
                    ("legacy", "screen_name"),
                ),
                default=deep_get(raw, (("authorName",), ("author_username",), ("userName",))),
            )
        )
        .removeprefix("@")
        .lower()
    )
    tweet_id = str(
        deep_get(raw, (("id",), ("id_str",), ("tweetId",), ("rest_id",), ("legacy", "id_str")))
    )
    text = str(
        deep_get(
            raw,
            (
                ("text",),
                ("fullText",),
                ("full_text",),
                ("legacy", "full_text"),
                ("note_tweet", "note_tweet_results", "result", "text"),
            ),
        )
    )
    created_at = parse_datetime(
        deep_get(raw, (("createdAt",), ("created_at",), ("legacy", "created_at")))
    )
    reply_to = str(
        deep_get(
            raw,
            (
                ("inReplyToId",),
                ("in_reply_to_status_id_str",),
                ("replyToTweetId",),
                ("legacy", "in_reply_to_status_id_str"),
            ),
        )
    )
    quoted_id = str(
        deep_get(
            raw,
            (
                ("quotedTweetId",),
                ("quoted_tweet_id",),
                ("quoted_status_id_str",),
                ("legacy", "quoted_status_id_str"),
                ("quoted_tweet", "id"),
            ),
        )
    )
    media = deep_get(
        raw,
        (
            ("media",),
            ("extendedEntities", "media"),
            ("extended_entities", "media"),
            ("legacy", "extended_entities", "media"),
        ),
        default=[],
    )
    is_retweet = bool(
        raw.get("retweeted_tweet")
        or raw.get("retweetedTweet")
        or raw.get("retweeted_status")
        or legacy.get("retweeted_status_result")
        or text.startswith("RT @")
    )
    if not tweet_id:
        raise ValueError("twitterapi.io tweet payload did not include a tweet ID")
    return Tweet(
        tweet_id=tweet_id,
        text=text,
        author_id=author_id,
        author_handle=author_handle,
        created_at=created_at,
        has_media=bool(media),
        is_reply=bool(reply_to or raw.get("isReply") or legacy.get("is_reply")),
        reply_to_tweet_id=reply_to,
        quoted_tweet_id=quoted_id,
        is_retweet=is_retweet,
    )


class TwitterApiClient:
    def __init__(self, api_key: str, *, timeout_seconds: int = 30) -> None:
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self.request_count = 0
        self.items_returned = 0

    async def __aenter__(self) -> TwitterApiClient:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"x-api-key": self._api_key, "Accept": "application/json"},
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def reset_usage(self) -> None:
        self.request_count = 0
        self.items_returned = 0

    async def _request_json(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self.start()
        assert self._session is not None
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                self.request_count += 1
                async with self._session.get(f"{BASE_URL}{path}", params=params) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        body = (await response.text())[:500]
                        raise TwitterApiError(
                            f"Non-JSON response: {body}", status=response.status, path=path
                        ) from exc
                    if response.status == 402:
                        raise TwitterApiError(
                            "twitterapi.io balance is empty; top it up in the dashboard",
                            status=402,
                            path=path,
                        )
                    if response.status == 401:
                        raise TwitterApiError(
                            "twitterapi.io rejected TWITTERAPI_IO_KEY",
                            status=401,
                            path=path,
                        )
                    if response.status == 429 or response.status >= 500:
                        detail = payload.get("detail") or payload.get("msg") or str(payload)
                        raise TwitterApiError(str(detail), status=response.status, path=path)
                    if response.status >= 400:
                        detail = payload.get("detail") or payload.get("msg") or str(payload)
                        raise TwitterApiError(str(detail), status=response.status, path=path)
                    if payload.get("status") == "error":
                        raise TwitterApiError(
                            str(
                                payload.get("msg")
                                or payload.get("message")
                                or "twitterapi.io semantic error"
                            ),
                            status=response.status,
                            path=path,
                        )
                    if not isinstance(payload, dict):
                        raise TwitterApiError("Unexpected JSON response shape", path=path)
                    return payload
            except (TimeoutError, aiohttp.ClientError, TwitterApiError) as exc:
                last_error = exc
                retryable = (
                    not isinstance(exc, TwitterApiError)
                    or exc.status in {0, 429}
                    or exc.status >= 500
                )
                if not retryable or attempt == 4:
                    raise
                delay = min(12.0, (2**attempt) + random.random())
                LOGGER.warning(
                    "twitterapi.io retry path=%s attempt=%s delay=%.1fs", path, attempt + 1, delay
                )
                await asyncio.sleep(delay)
        raise TwitterApiError(str(last_error or "Unknown twitterapi.io error"), path=path)

    @staticmethod
    def _page_value(payload: dict[str, Any], key: str, default: Any = None) -> Any:
        if key in payload:
            return payload[key]
        data = payload.get("data")
        if isinstance(data, dict) and key in data:
            return data[key]
        return default

    async def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any],
        item_key: str | tuple[str, ...],
        max_pages: int,
        stop_when: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> PageResult:
        items: list[dict[str, Any]] = []
        cursor = ""
        complete = False
        pages = 0
        for _ in range(max(1, max_pages)):
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            payload = await self._request_json(path, params=query)
            pages += 1
            item_keys = (item_key,) if isinstance(item_key, str) else item_key
            page_items: Any = []
            for key in item_keys:
                page_items = self._page_value(payload, key, [])
                if isinstance(page_items, list) and page_items:
                    break
            if not isinstance(page_items, list):
                page_items = []
            clean_items = [item for item in page_items if isinstance(item, dict)]
            items.extend(clean_items)
            self.items_returned += len(clean_items)

            # Official docs note that X can report has_next_page=true and then return
            # an empty terminal page. Treat that documented case as complete.
            if not clean_items:
                complete = True
                break

            if stop_when and stop_when(clean_items):
                complete = True
                break
            has_next = bool(self._page_value(payload, "has_next_page", False))
            if not has_next:
                complete = True
                break
            cursor = str(self._page_value(payload, "next_cursor", "") or "")
            if not cursor:
                break
        return PageResult(items=items, complete=complete, pages=pages)

    async def get_user_info(self, handle: str) -> dict[str, Any]:
        payload = await self._request_json(
            "/twitter/user/info", params={"userName": handle.removeprefix("@").lower()}
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TwitterApiError("User lookup returned no profile", path="/twitter/user/info")
        return data

    async def get_recent_tweets(
        self, handle: str, *, since: datetime, until: datetime, max_pages: int
    ) -> tuple[list[Tweet], bool]:
        def reached_cutoff(page_items: list[dict[str, Any]]) -> bool:
            parsed_dates = []
            for item in page_items:
                try:
                    parsed_dates.append(parse_tweet(item).created_at)
                except (ValueError, TypeError):
                    continue
            return bool(parsed_dates) and min(parsed_dates) < since

        result = await self._paginate(
            "/twitter/user/last_tweets",
            params={"userName": handle, "includeReplies": False},
            item_key="tweets",
            max_pages=max_pages,
            stop_when=reached_cutoff,
        )
        tweets = []
        for item in result.items:
            try:
                tweet = parse_tweet(item)
            except (ValueError, TypeError) as exc:
                LOGGER.warning("Skipping malformed source tweet: %s", exc)
                continue
            if since <= tweet.created_at <= until and not tweet.is_reply and not tweet.is_retweet:
                tweets.append(tweet)
        return tweets, result.complete

    async def get_replies(
        self, tweet_id: str, *, since: datetime, until: datetime, max_pages: int
    ) -> PageResult:
        return await self._paginate(
            "/twitter/tweet/replies",
            params={
                "tweetId": tweet_id,
                "sinceTime": int(since.timestamp()),
                "untilTime": int(until.timestamp()),
                "queryType": "Latest",
            },
            item_key=("tweets", "replies"),
            max_pages=max_pages,
        )

    async def get_quotes(self, tweet_id: str, *, since: datetime, max_pages: int) -> PageResult:
        def reached_cutoff(page_items: list[dict[str, Any]]) -> bool:
            dates = []
            for item in page_items:
                try:
                    dates.append(parse_tweet(item).created_at)
                except (ValueError, TypeError):
                    continue
            return bool(dates) and min(dates) < since

        return await self._paginate(
            "/twitter/tweet/quotes",
            params={"tweetId": tweet_id},
            item_key="tweets",
            max_pages=max_pages,
            stop_when=reached_cutoff,
        )

    async def get_retweeters(self, tweet_id: str, *, max_pages: int) -> PageResult:
        return await self._paginate(
            "/twitter/tweet/retweeters",
            params={"tweetId": tweet_id},
            item_key="users",
            max_pages=max_pages,
        )

    async def get_mentions(
        self, handle: str, *, since: datetime, until: datetime, max_pages: int
    ) -> PageResult:
        return await self._paginate(
            "/twitter/user/mentions",
            params={
                "userName": handle,
                "sinceTime": int(since.timestamp()),
                "untilTime": int(until.timestamp()),
                "queryType": "Latest",
            },
            item_key="tweets",
            max_pages=max_pages,
        )

    async def get_tweets(self, tweet_ids: list[str]) -> list[Tweet]:
        output: list[Tweet] = []
        for offset in range(0, len(tweet_ids), 100):
            batch = tweet_ids[offset : offset + 100]
            payload = await self._request_json(
                "/twitter/tweets", params={"tweet_ids": ",".join(batch)}
            )
            raw_tweets = payload.get("tweets", [])
            if not isinstance(raw_tweets, list):
                continue
            self.items_returned += len(raw_tweets)
            for item in raw_tweets:
                if not isinstance(item, dict):
                    continue
                try:
                    output.append(parse_tweet(item))
                except (ValueError, TypeError) as exc:
                    LOGGER.warning("Skipping malformed tracked tweet: %s", exc)
        return output
