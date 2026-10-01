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
