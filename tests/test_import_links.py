from __future__ import annotations

from typing import Any

import pytest

from majors_lair_bot.database import DatabaseRepository, create_database_engine
from majors_lair_bot.engagement import EngagementService
from majors_lair_bot.twitter_client import TwitterApiError


class FakeTwitter:
    """Resolves handles from a fixed directory and counts lookups."""

    def __init__(self, directory: dict[str, str], *, fail_with: int | None = None) -> None:
        self.directory = directory
        self.fail_with = fail_with
        self.lookups: list[str] = []

    profiles_by_id: dict[str, dict[str, Any]] = {}
    batch_calls = 0

    async def get_users_by_ids(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        self.batch_calls += 1
        return {uid: self.profiles_by_id[uid] for uid in user_ids if uid in self.profiles_by_id}

    async def get_user_info(self, handle: str) -> dict[str, Any]:
        self.lookups.append(handle)
        if self.fail_with:
            raise TwitterApiError("boom", status=self.fail_with, path="/twitter/user/info")
        user_id = self.directory.get(handle.lower())
        if not user_id:
            raise TwitterApiError("user not found", status=404, path="/twitter/user/info")
        return {"id": user_id, "userName": handle.lower()}


@pytest.fixture
async def repository() -> DatabaseRepository:
    repo = DatabaseRepository(create_database_engine("sqlite+aiosqlite:///:memory:"))
    await repo.ensure_schema()
    yield repo
    await repo.close()


def _row(discord_user_id: str, username: str, handle: str, **extra: object) -> dict[str, object]:
    return {
        "discord_user_id": discord_user_id,
        "discord_username": username,
        "twitter_handle": handle,
        **extra,
    }


@pytest.mark.asyncio
async def test_import_links_reports_every_row_in_order(repository: DatabaseRepository) -> None:
    twitter = FakeTwitter({"alpha": "1", "bravo": "2", "charlie": "3"})
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    await repository.link_user(
        discord_user_id="900000",
        discord_username="Taken",
        twitter_handle="bravo",
        twitter_user_id="2",
    )
    await repository.link_user(
        discord_user_id="100003",
        discord_username="Charlie",
        twitter_handle="charlie",
        twitter_user_id="3",
    )

    results = await service.import_links(
        [
            _row("100001", "Alpha", "@Alpha", special_role=True, special_role_names="Builder"),
            _row("100002", "Bravo", "bravo"),
            _row("100003", "Charlie", "charlie", special_role=True, special_role_names="Team"),
            _row("100004", "Delta", "", special_role=False, special_role_names=""),
            _row("100005", "Echo", "no such handle!"),
            _row("100006", "Foxtrot", "ghost"),
        ],
        actor_discord_id="1",
    )

    assert [r["status"] for r in results] == [
        "linked",
        "conflict",
        "unchanged",
        "registered",
        "failed",
        "failed",
    ]
    assert results[0]["twitter_handle"] == "alpha"
    assert results[5]["message"].startswith("X account not found")
    # Unchanged, handle-less, and invalid rows never spend a twitterapi.io lookup.
    assert sorted(twitter.lookups) == ["alpha", "bravo", "ghost"]

    linked = await repository.get_user("100001")
    assert linked is not None and linked.twitter_user_id == "1"
    assert linked.special_role and linked.special_role_names == "Builder"

    # Every member in the sheet exists afterwards, even when the X link did not happen.
    for discord_user_id in ("100002", "100004", "100005", "100006"):
        member = await repository.get_user(discord_user_id)
        assert member is not None and member.active and not member.twitter_user_id

    # Re-importing an already linked member refreshes the special-role flags only.
    charlie = await repository.get_user("100003")
    assert charlie is not None and charlie.twitter_user_id == "3"
    assert charlie.special_role and charlie.special_role_names == "Team"

    audit = await repository.paginated_audit(event_type="admin_members_imported")
    assert audit["total"] == 1


@pytest.mark.asyncio
async def test_import_links_relinks_changed_handles(repository: DatabaseRepository) -> None:
    twitter = FakeTwitter({"old_name": "7", "new_name": "7"})
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    await repository.link_user(
        discord_user_id="100007",
        discord_username="Golf",
        twitter_handle="old_name",
        twitter_user_id="7",
    )

    results = await service.import_links([_row("100007", "Golf", "new_name")], actor_discord_id="1")

    assert results[0]["status"] == "relinked"
    assert results[0]["message"] == "Was @old_name"
    user = await repository.get_user("100007")
    assert user is not None and user.twitter_handle == "new_name"


@pytest.mark.asyncio
async def test_import_links_aborts_when_balance_is_empty(repository: DatabaseRepository) -> None:
    twitter = FakeTwitter({}, fail_with=402)
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    rows = [_row(str(100010 + i), f"user{i}", f"handle{i}") for i in range(12)]

    results = await service.import_links(rows, actor_discord_id="1", concurrency=1)

    assert all(r["status"] == "failed" for r in results)
    assert all("balance" in r["message"].lower() or "boom" in r["message"] for r in results)
    # Only the first lookup reaches twitterapi.io; the rest short-circuit.
    assert len(twitter.lookups) == 1


@pytest.mark.asyncio
async def test_verify_linked_accounts_flags_suspended_and_renames(
    repository: DatabaseRepository,
) -> None:
    twitter = FakeTwitter({})
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    for uid, name, handle in (("1", "Alive", "alive"), ("2", "Gone", "gone"), ("3", "New", "old")):
        await repository.link_user(
            discord_user_id=uid, discord_username=name, twitter_handle=handle, twitter_user_id=uid
        )
    await repository.register_member(discord_user_id="4", discord_username="NoX")
    twitter.profiles_by_id = {
        "1": {"id": "1", "userName": "alive"},
        "2": {"id": "2", "unavailable": True, "unavailableReason": "Suspended"},
        "3": {"id": "3", "userName": "NewName"},
    }

    outcome = await service.verify_linked_accounts(actor_discord_id="admin")

    assert outcome["checked"] == 3 and twitter.batch_calls == 1
    assert [item["discord_user_id"] for item in outcome["unavailable"]] == ["2"]
    assert outcome["unavailable"][0]["status"] == "suspended"
    assert outcome["renamed"] == [
        {
            "discord_user_id": "3",
            "discord_username": "New",
            "old_handle": "old",
            "new_handle": "newname",
        }
    ]
    gone = await repository.get_user("2")
    assert gone is not None and gone.x_status == "suspended" and gone.x_checked_at
    renamed = await repository.get_user("3")
    assert renamed is not None and renamed.twitter_handle == "newname"
    assert renamed.handle_history == "old" and renamed.x_status == "ok"
    # Members without an X account are never sent to twitterapi.io.
    nox = await repository.get_user("4")
    assert nox is not None and nox.x_status == ""

    # Suspended members show up in the "X issues" filter and the low-activity report.
    page = await repository.paginated_users(x_ok=False)
    assert [item["discord_user_id"] for item in page["items"]] == ["2"]
    report = await repository.low_activity(5)
    assert {u.discord_user_id for u in report} == {"1", "2", "3", "4"}

    # Protected members can be left out of an on-demand check.
    await repository.set_special_role("1", special_role=True, special_role_names="Team")
    scoped = await service.verify_linked_accounts(include_protected=False)
    assert scoped["checked"] == 2 and scoped["include_protected"] is False

    # A twitterapi.io failure is reported, not raised.
    async def boom(_: list[str]) -> dict[str, dict[str, Any]]:
        raise TwitterApiError("balance empty", status=402, path="/x")

    twitter.get_users_by_ids = boom  # type: ignore[method-assign]
    failed = await service.verify_linked_accounts()
    assert failed["checked"] == 0 and "balance" in failed["error"]


@pytest.mark.asyncio
async def test_estimate_scan_sums_post_counters_and_caches(repository: DatabaseRepository) -> None:
    from datetime import UTC, datetime

    from majors_lair_bot.models import Tweet

    class CountingTwitter(FakeTwitter):
        calls = 0

        async def get_recent_tweets(self, handle: str, **_: object) -> tuple[list[Tweet], bool]:
            self.calls += 1
            self.request_count += 1
            now = datetime.now(UTC)
            if handle != "m_m3l":
                return [], True
            return (
                [
                    Tweet("1", "a", "9", handle, now, reply_count=30, quote_count=2),
                    Tweet("2", "b", "9", handle, now, reply_count=1000, retweet_count=5),
                ],
                True,
            )

    twitter = CountingTwitter({})
    twitter.request_count = 0
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]

    estimate = await service.estimate_scan("7d")

    # cap = max_action_pages_per_post (8) * 20 = 160 items per endpoint
    assert estimate["source_posts"] == 2
    assert estimate["engagement_items"] == (30 + 2 + 1) + (160 + 1 + 5)
    assert estimate["engagement_credits"] == estimate["engagement_items"] * 15
    assert estimate["credits_high"] > estimate["credits_low"] > 0
    assert estimate["cached"] is False and twitter.calls == 2

    again = await service.estimate_scan("7d")
    assert again["cached"] is True and twitter.calls == 2


@pytest.mark.asyncio
async def test_reply_sweep_counts_hidden_replies_once(repository: DatabaseRepository) -> None:
    from datetime import UTC, datetime, timedelta

    from majors_lair_bot.models import ActionType, ScanSummary
    from majors_lair_bot.scoring import DEFAULT_CONFIG, ScoringRules
    from majors_lair_bot.twitter_client import PageResult

    now = datetime.now(UTC)
    raw_hidden = {
        "id": "901",
        "text": "cokkk",
        "createdAt": (now - timedelta(days=2)).strftime("%a %b %d %H:%M:%S %z %Y"),
        "inReplyToId": "500",
        "author": {"id": "77", "userName": "oluwa"},
    }
    raw_seen = {**raw_hidden, "id": "902"}
    raw_by_target = {**raw_hidden, "id": "903", "author": {"id": "1", "userName": "majorslair"}}

    class SweepTwitter(FakeTwitter):
        async def search_replies_to(self, handle: str, **_: object) -> PageResult:
            if handle != "majorslair":
                return PageResult(items=[], complete=True, pages=1)
            return PageResult(items=[raw_hidden, raw_seen, raw_by_target], complete=True, pages=1)

    twitter = SweepTwitter({})
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    await repository.link_user(
        discord_user_id="7", discord_username="oluwa", twitter_handle="oluwa", twitter_user_id="77"
    )
    users = await repository.list_users(active_only=True)
    by_id, by_handle = service._user_indexes(users)
    summary = ScanSummary(period_label="7d")
    counted = {"902"}  # already found by the per-post reply endpoint

    actions = await service._collect_reply_sweep(
        rules=ScoringRules.from_mapping(DEFAULT_CONFIG),
        cycle_id="cycle_initial",
        since=now - timedelta(days=7),
        until=now,
        max_pages=5,
        by_id=by_id,
        by_handle=by_handle,
        already_counted_tweet_ids=counted,
        summary=summary,
    )

    assert [a.action_tweet_id for a in actions] == ["901"]
    assert actions[0].action_type is ActionType.REPLY
    assert actions[0].source_post_id == "500" and actions[0].target_handle == "majorslair"
    assert summary.swept_replies == 1 and "901" in counted


@pytest.mark.asyncio
async def test_member_timeline_path_counts_replies_to_targets_only(
    repository: DatabaseRepository,
) -> None:
    from datetime import UTC, datetime, timedelta

    from majors_lair_bot.models import ScanSummary, Tweet
    from majors_lair_bot.scoring import DEFAULT_CONFIG, ScoringRules

    now = datetime.now(UTC)

    class TimelineTwitter(FakeTwitter):
        async def get_user_timeline_with_replies(
            self, handle: str, **_: object
        ) -> tuple[list[Tweet], bool]:
            self.lookups.append(handle)
            return (
                [
                    Tweet(
                        "1",
                        "cokkk",
                        "77",
                        handle,
                        now,
                        is_reply=True,
                        reply_to_tweet_id="500",
                        reply_to_handle="majorslair",
                    ),
                    Tweet(
                        "2",
                        "nice",
                        "77",
                        handle,
                        now,
                        is_reply=True,
                        reply_to_tweet_id="600",
                        reply_to_handle="someone_else",
                    ),
                    Tweet(
                        "3",
                        "already",
                        "77",
                        handle,
                        now,
                        is_reply=True,
                        reply_to_tweet_id="700",
                        reply_to_handle="m_m3l",
                    ),
                    Tweet("4", "standalone", "77", handle, now),
                ],
                True,
            )

    twitter = TimelineTwitter({})
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    await repository.link_user(
        discord_user_id="7", discord_username="oluwa", twitter_handle="oluwa", twitter_user_id="77"
    )
    await repository.register_member(discord_user_id="8", discord_username="nox")
    users = await repository.list_users(active_only=True)
    summary = ScanSummary(period_label="7d")
    counted = {"3"}

    actions = await service._collect_member_timelines(
        rules=ScoringRules.from_mapping(DEFAULT_CONFIG),
        cycle_id="cycle_initial",
        since=now - timedelta(days=7),
        until=now,
        max_pages=2,
        users=users,
        already_counted_tweet_ids=counted,
        summary=summary,
    )

    assert twitter.lookups == ["oluwa"]  # members without X are never fetched
    assert [(a.action_tweet_id, a.target_handle, a.source_post_id) for a in actions] == [
        ("1", "majorslair", "500")
    ]
    assert summary.timeline_replies == 1 and summary.timeline_members_checked == 1

    # Off by default: max_pages 0 does nothing and costs nothing.
    twitter.lookups.clear()
    assert (
        await service._collect_member_timelines(
            rules=ScoringRules.from_mapping(DEFAULT_CONFIG),
            cycle_id="c",
            since=now,
            until=now,
            max_pages=0,
            users=users,
            already_counted_tweet_ids=set(),
            summary=summary,
        )
        == []
    )
    assert twitter.lookups == []
