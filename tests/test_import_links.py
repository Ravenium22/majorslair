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
            ("100001", "Alpha", "@Alpha"),
            ("100002", "Bravo", "bravo"),
            ("100003", "Charlie", "charlie"),
            ("100004", "Delta", ""),
            ("100005", "Echo", "no such handle!"),
            ("100006", "Foxtrot", "ghost"),
        ],
        actor_discord_id="1",
    )

    assert [r["status"] for r in results] == [
        "linked",
        "conflict",
        "unchanged",
        "skipped",
        "failed",
        "failed",
    ]
    assert results[0]["twitter_handle"] == "alpha"
    assert results[5]["message"] == "X account not found"
    # Unchanged, skipped, and invalid rows never spend a twitterapi.io lookup.
    assert sorted(twitter.lookups) == ["alpha", "bravo", "ghost"]

    linked = await repository.get_user("100001")
    assert linked is not None and linked.twitter_user_id == "1"
    assert await repository.get_user("100002") is None

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

    results = await service.import_links([("100007", "Golf", "new_name")], actor_discord_id="1")

    assert results[0]["status"] == "relinked"
    assert results[0]["message"] == "Was @old_name"
    user = await repository.get_user("100007")
    assert user is not None and user.twitter_handle == "new_name"


@pytest.mark.asyncio
async def test_import_links_aborts_when_balance_is_empty(repository: DatabaseRepository) -> None:
    twitter = FakeTwitter({}, fail_with=402)
    service = EngagementService(repository, twitter)  # type: ignore[arg-type]
    rows = [(str(100010 + i), f"user{i}", f"handle{i}") for i in range(12)]

    results = await service.import_links(rows, actor_discord_id="1", concurrency=1)

    assert all(r["status"] == "failed" for r in results)
    assert all("balance" in r["message"].lower() or "boom" in r["message"] for r in results)
    # Only the first lookup reaches twitterapi.io; the rest short-circuit.
    assert len(twitter.lookups) == 1
