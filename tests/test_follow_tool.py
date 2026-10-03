from __future__ import annotations

import pytest

from majors_lair_bot.database import DatabaseRepository, create_database_engine
from majors_lair_bot.engagement import EngagementService
from majors_lair_bot.twitter_client import TwitterApiError


class FakeTwitter:
    """Answers "does A follow B" from a fixed set of edges; unknown users raise."""

    def __init__(self, follows: set[tuple[str, str]], missing: set[str]) -> None:
        self.follows = follows
        self.missing = missing
        self.request_count = 0
        self.items_returned = 0

    def reset_usage(self) -> None:
        self.request_count = 0

    async def is_following(self, source: str, target: str) -> bool:
        self.request_count += 1
        if source in self.missing:
            raise TwitterApiError("User not found", status=404, path="/twitter/user/check_follow_relationship")
        return (source, target) in self.follows


@pytest.fixture
async def repository() -> DatabaseRepository:
    repo = DatabaseRepository(create_database_engine("sqlite+aiosqlite:///:memory:"))
    await repo.ensure_schema()
    yield repo
    await repo.close()


@pytest.mark.asyncio
async def test_raffle_winners_are_checked_without_touching_members(repository: DatabaseRepository) -> None:
    twitter = FakeTwitter(
        follows={("win1", "major"), ("win1", "project"), ("win2", "major")},
        missing={"gone"},
    )
    service = EngagementService(repository, twitter)

    out = await service.check_handles_follow(
        handles=["win1", "win2", "gone"], accounts=["major", "project"], actor_discord_id="1"
    )

    rows = {row["handle"]: row for row in out["rows"]}
    assert rows["win1"]["follows_all"] is True
    assert rows["win2"]["results"] == {"major": True, "project": False}
    assert rows["win2"]["follows_all"] is False
    assert rows["gone"]["follows_all"] is False and "not found" in rows["gone"]["error"]
    assert out["follows_all"] == 1 and out["errors"] == 1
    assert out["calls"] == 6 and out["credits"] == 600
    assert await repository.list_users() == [], "the registry is never touched"
    audit = await repository.paginated_audit(page_size=5)
    assert audit["items"][0]["event_type"] == "follow_list_checked"


def test_pasted_lists_are_cleaned() -> None:
    from majors_lair_bot.roles import clean_handles

    pasted = ["@Alice", "https://x.com/bob_1/status/9", "alice", "not a handle!", "twitter.com/Carol", ""]
    assert clean_handles(pasted) == ["Alice", "bob_1", "Carol"]


def _raw(tweet_id: str, author: str, text: str, reply_to: str = "", minutes: int = 0) -> dict:
    return {
        "id": tweet_id, "text": text, "author": {"userName": author, "id": f"u-{author}"},
        "createdAt": f"2026-09-30T12:{minutes:02d}:00Z", "inReplyToId": reply_to,
        "replyCount": 3, "retweetCount": 2,
    }


class RaffleTwitter(FakeTwitter):
    def __init__(self, *, retweeters: list[str], retweet_count: int, retweeters_complete: bool = True, by_post: dict[str, list[str]] | None = None, thread_fails: bool = False) -> None:
        super().__init__(follows={("ana", "major"), ("ana", "proj"), ("ben", "major"), ("ben", "proj"), ("cy", "major")}, missing=set())
        self.retweeters = retweeters
        self.by_post = by_post or {}
        self.thread_fails = thread_fails
        self.retweet_count = retweet_count
        self.retweeters_complete = retweeters_complete

    async def get_tweets(self, ids: list[str]):
        from majors_lair_bot.twitter_client import parse_tweet
        raw = _raw(ids[0], "major", "Raffle! Reply, follow, RT")
        raw["retweetCount"] = self.retweet_count
        return [parse_tweet(raw)]

    async def get_replies_v2(self, tweet_id: str, *, query_type: str = "Latest", max_pages: int):
        from majors_lair_bot.models import PageResult
        if self.thread_fails:
            raise TwitterApiError("Bad gateway", status=502, path="/twitter/tweet/replies/v2")
        # The thread view returns fay, whom the time-window list and both searches miss.
        return PageResult(items=[_raw("r8", "fay", "in", tweet_id, 8), _raw("r1", "ana", "done", tweet_id, 1)], complete=True, pages=1)

    async def get_replies(self, tweet_id: str, *, since, until, max_pages: int, empty_pages_allowed: int = 0):
        from majors_lair_bot.models import PageResult
        return PageResult(items=[
            _raw("r1", "ana", "done", tweet_id, 1),
            _raw("r2", "ana", "again", tweet_id, 2),        # a second reply: still one entry
            _raw("r3", "major", "good luck", tweet_id, 3),  # the author is not a participant
            _raw("r4", "dan", "reply to ana", "r1", 4),     # replying to someone else
        ], complete=True, pages=1)

    async def search_conversation(self, tweet_id: str, *, max_pages: int):
        from majors_lair_bot.models import PageResult
        # X hid this reply from the list; only search finds it.
        return PageResult(items=[_raw("r5", "ben", "in @x @y", tweet_id, 5), _raw("r1", "ana", "done", tweet_id, 1)], complete=True, pages=1)

    async def search_replies_to(self, handle: str, *, since, until, max_pages: int, empty_pages_allowed: int = 0):
        from majors_lair_bot.models import PageResult
        # The account-wide "to:" search: another hidden entrant, a reply to a different
        # post (ignored), and ana again (already counted).
        return PageResult(items=[_raw("r6", "eve", "hidden too", "100", 6), _raw("r7", "zed", "other post", "999", 7), _raw("r1", "ana", "done", "100", 1)], complete=True, pages=1)

    async def get_retweeters(self, tweet_id: str, *, max_pages: int):
        from majors_lair_bot.models import PageResult
        names = self.by_post.get(tweet_id, self.retweeters)
        return PageResult(items=[{"userName": name} for name in names], complete=self.retweeters_complete, pages=1)


@pytest.mark.asyncio
async def test_participants_are_direct_replies_once_each_including_hidden_ones(repository: DatabaseRepository) -> None:
    service = EngagementService(repository, RaffleTwitter(retweeters=[], retweet_count=0))
    out = await service.raffle_participants(url="https://x.com/major/status/100", exclude=["proj"])
    assert [p["handle"] for p in out["participants"]] == ["ana", "ben", "eve", "fay"]
    assert out["participants"][0]["reply"] == "done", "the first reply is the one kept"
    assert out["found_only_by_search"] == 2
    assert out["breakdown"] == {"from_list": 2, "only_by_search": 2, "second_replies": 1, "left_out": 1, "nested": 1}
    report = {row["source"]: row for row in out["sources"]}
    assert [row["source"] for row in out["sources"]] == ["thread_latest", "thread_top", "reply_list", "conversation", "to_author"]
    assert report["thread_latest"]["new"] == 2 and report["thread_top"]["new"] == 0
    assert report["conversation"]["new"] == 1 and report["to_author"]["new"] == 1


@pytest.mark.asyncio
async def test_participants_survive_a_failing_source(repository: DatabaseRepository) -> None:
    service = EngagementService(repository, RaffleTwitter(retweeters=[], retweet_count=0, thread_fails=True))
    out = await service.raffle_participants(url="https://x.com/major/status/100", exclude=[])
    assert [p["handle"] for p in out["participants"]] == ["ana", "ben", "eve"]
    report = {row["source"]: row for row in out["sources"]}
    assert "Bad gateway" in report["thread_latest"]["error"] and report["thread_latest"]["complete"] is False
    assert out["complete"] is True, "the sources that answered were read to the end"


@pytest.mark.asyncio
async def test_two_posts_to_retweet_must_both_be_retweeted(repository: DatabaseRepository) -> None:
    twitter = RaffleTwitter(retweeters=[], retweet_count=2, by_post={"100": ["ana", "ben"], "200": ["ana", "cy"]})
    service = EngagementService(repository, twitter)
    out = await service.check_handles_follow(
        handles=["ana", "ben"], accounts=["major", "proj"], actor_discord_id="1",
        retweet_urls=["https://x.com/major/status/100", "https://x.com/proj/status/200", "https://x.com/major/status/100"],
    )
    assert [post["tweet_id"] for post in out["retweet_posts"]] == ["100", "200"], "duplicates read once"
    rows = {row["handle"]: row for row in out["rows"]}
    assert rows["ana"]["retweets"] == {"100": True, "200": True} and rows["ana"]["verdict"] == "passes"
    assert rows["ben"]["retweets"] == {"100": True, "200": False}
    assert rows["ben"]["retweeted"] is False and rows["ben"]["verdict"] == "missing"


@pytest.mark.asyncio
async def test_retweet_requirement_never_guesses_from_a_short_list(repository: DatabaseRepository) -> None:
    # The list X returned is whole (as long as the retweet count): absence means no.
    whole = EngagementService(repository, RaffleTwitter(retweeters=["ana", "cy"], retweet_count=2))
    out = await whole.check_handles_follow(handles=["ana", "ben", "cy"], accounts=["major", "proj"], actor_discord_id="1", retweet_url="https://x.com/major/status/100")
    rows = {row["handle"]: row for row in out["rows"]}
    assert rows["ana"]["verdict"] == "passes"
    assert rows["ben"]["retweeted"] is False and rows["ben"]["verdict"] == "missing"
    assert rows["cy"]["verdict"] == "missing", "retweeted but does not follow both"

    # X returned fewer retweeters than the post has: ben's absence proves nothing.
    short = EngagementService(repository, RaffleTwitter(retweeters=["ana"], retweet_count=40))
    out = await short.check_handles_follow(handles=["ana", "ben"], accounts=["major", "proj"], actor_discord_id="1", retweet_url="https://x.com/major/status/100")
    rows = {row["handle"]: row for row in out["rows"]}
    assert rows["ana"]["verdict"] == "passes"
    assert rows["ben"]["retweeted"] is None and rows["ben"]["verdict"] == "check"
    assert out["passes"] == 1 and out["to_check"] == 1


def test_draw_picks_distinct_winners_fairly() -> None:
    from collections import Counter
    from random import Random

    from majors_lair_bot.raffle import draw_winners

    pool = ["a", "b", "c", "d", "a"]  # a duplicate entry does not double a chance
    winners = draw_winners(pool, 3, Random(1))
    assert len(winners) == 3 and len(set(winners)) == 3 and set(winners) <= {"a", "b", "c", "d"}

    rng = Random(7)
    tally = Counter(draw_winners(pool, 1, rng)[0] for _ in range(20000))
    assert set(tally) == {"a", "b", "c", "d"}
    assert all(4300 < n < 5700 for n in tally.values()), tally  # each about a quarter

    with pytest.raises(ValueError, match="only 4 eligible"):
        draw_winners(pool, 5)
    with pytest.raises(ValueError):
        draw_winners(pool, 0)



@pytest.mark.asyncio
async def test_pager_can_step_over_pages_the_api_filtered_empty() -> None:
    """twitterapi.io filters each page after X serves it; an empty page is not always the end."""
    from majors_lair_bot.twitter_client import TwitterApiClient

    pages = [
        {"replies": [{"id": "1"}], "has_next_page": True, "next_cursor": "a"},
        {"replies": [], "has_next_page": True, "next_cursor": "b"},
        {"replies": [{"id": "2"}], "has_next_page": False, "next_cursor": ""},
    ]

    class Paged(TwitterApiClient):
        def __init__(self) -> None:
            self.items_returned = 0
            self.request_count = 0
            self.calls = 0

        async def _request_json(self, path, *, params=None):
            self.calls += 1
            return pages[self.calls - 1]

    strict = Paged()
    result = await strict._paginate("/x", params={}, item_key="replies", max_pages=10)
    assert [i["id"] for i in result.items] == ["1"], "default: an empty page ends the walk"

    tolerant = Paged()
    result = await tolerant._paginate("/x", params={}, item_key="replies", max_pages=10, empty_pages_allowed=3)
    assert [i["id"] for i in result.items] == ["1", "2"] and result.complete
