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

    # A twitterapi.io failure is reported, not raised.
    async def boom(_: list[str]) -> dict[str, dict[str, Any]]:
        raise TwitterApiError("balance empty", status=402, path="/x")

    twitter.get_users_by_ids = boom  # type: ignore[method-assign]
    failed = await service.verify_linked_accounts()
    assert failed["checked"] == 0 and "balance" in failed["error"]
