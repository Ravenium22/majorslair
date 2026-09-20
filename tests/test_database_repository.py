from __future__ import annotations

import pytest

from majors_lair_bot.database import DatabaseRepository, LinkConflictError, create_database_engine


@pytest.fixture
async def repository() -> DatabaseRepository:
    repo = DatabaseRepository(create_database_engine("sqlite+aiosqlite:///:memory:"))
    await repo.ensure_schema()
    yield repo
    await repo.close()


@pytest.mark.asyncio
async def test_linking_enforces_one_active_member_per_x_account(
    repository: DatabaseRepository,
) -> None:
    await repository.link_user(
        discord_user_id="111111",
        discord_username="Major",
        twitter_handle="m_m3l",
        twitter_user_id="999999",
    )

    with pytest.raises(LinkConflictError):
        await repository.link_user(
            discord_user_id="222222",
            discord_username="Other",
            twitter_handle="different_handle",
            twitter_user_id="999999",
        )


@pytest.mark.asyncio
async def test_reset_preserves_snapshot_and_starts_new_cycle(
    repository: DatabaseRepository,
) -> None:
    await repository.link_user(
        discord_user_id="111111",
        discord_username="Major",
        twitter_handle="m_m3l",
        twitter_user_id="999999",
    )
    before = (await repository.get_config())["current_cycle_id"]

    old_cycle, new_cycle, snapshots = await repository.reset_leaderboard("555555")

    assert old_cycle == before
    assert new_cycle != before
    assert snapshots == 1
    assert (await repository.get_config())["current_cycle_id"] == new_cycle


@pytest.mark.asyncio
async def test_admin_sessions_store_and_resolve_opaque_tokens(
    repository: DatabaseRepository,
) -> None:
    token, csrf = await repository.create_admin_session(
        discord_user_id="111111",
        discord_username="Major",
        avatar_url="",
        role_ids=["123"],
        is_guild_admin=True,
        ttl_hours=12,
    )

    assert token not in repository.hash_session_token(token)
    session = await repository.get_admin_session(token)
    assert session is not None
    assert session["csrf_token"] == csrf
    await repository.delete_admin_session(token)
    assert await repository.get_admin_session(token) is None


@pytest.mark.asyncio
async def test_registry_keeps_unlinked_members_and_protects_special_roles(
    repository: DatabaseRepository,
) -> None:
    await repository.link_user(
        discord_user_id="1",
        discord_username="Linked",
        twitter_handle="linked",
        twitter_user_id="10",
    )
    unlinked, created = await repository.register_member(
        discord_user_id="2", discord_username="NoX"
    )
    assert created and unlinked.active and unlinked.twitter_user_id == ""
    protected, _ = await repository.register_member(
        discord_user_id="3",
        discord_username="Builder",
        special_role=True,
        special_role_names="Builder",
    )
    assert protected.special_role and protected.special_role_names == "Builder"

    # Registering again never touches the X link, only refreshes name and flags.
    again, created = await repository.register_member(
        discord_user_id="1", discord_username="Linked Renamed", special_role=True
    )
    assert not created and again.twitter_user_id == "10" and again.special_role

    # Leaderboard and overview only count members with an X account.
    assert [u.discord_user_id for u in await repository.leaderboard()] == ["1"]
    overview = await repository.overview()
    assert overview["linked_members"] == 1

    # Low-activity report: unlinked members first, protected members excluded by default.
    report = await repository.low_activity(5)
    assert [u.discord_user_id for u in report] == ["2"]
    everyone = await repository.low_activity(5, include_protected=True)
    assert {u.discord_user_id for u in everyone} == {"1", "2", "3"}

    # Unprotecting via the dashboard toggle clears the names too.
    toggled = await repository.set_special_role("3", special_role=False)
    assert toggled is not None and not toggled.special_role and toggled.special_role_names == ""

    # Unlinked members can be deactivated and reactivated without a duplicate-X check.
    assert (await repository.set_user_active("2", False)) is not None
    reactivated = await repository.set_user_active("2", True)
    assert reactivated is not None and reactivated.active

    # Unlinking someone who never linked is a no-op.
    assert await repository.unlink_user("2") == ""

    # An unlinked member can link later and shows up as a first-time link.
    old, new = await repository.link_user(
        discord_user_id="2", discord_username="NoX", twitter_handle="nowx", twitter_user_id="20"
    )
    assert (old, new) == ("", "nowx")

    # Filters on the paginated view.
    page = await repository.paginated_users(linked=False)
    assert [item["discord_user_id"] for item in page["items"]] == ["3"]
    page = await repository.paginated_users(protected=True)
    assert [item["discord_user_id"] for item in page["items"]] == ["1"]

    # Reset snapshots tolerate members without a handle.
    await repository.register_member(discord_user_id="4", discord_username="Late")
    _, _, snapshotted = await repository.reset_leaderboard("admin")
    assert snapshotted == 4


@pytest.mark.asyncio
async def test_member_filters_sorting_and_stale_scan_cleanup(
    repository: DatabaseRepository,
) -> None:
    await repository.link_user(
        discord_user_id="1", discord_username="zed", twitter_handle="zed", twitter_user_id="1"
    )
    await repository.link_user(
        discord_user_id="2", discord_username="amy", twitter_handle="amy", twitter_user_id="2"
    )
    await repository.register_member(
        discord_user_id="3", discord_username="mid", special_role=True, special_role_names="Team"
    )
    from majors_lair_bot.orm import UserRow

    async with repository.sessions.begin() as session:
        (await session.get(UserRow, "1")).score = 12
        (await session.get(UserRow, "2")).score = 3

    ids = lambda page: [item["discord_user_id"] for item in page["items"]]  # noqa: E731
    assert ids(await repository.paginated_users(points="positive")) == ["1", "2"]
    assert ids(await repository.paginated_users(points="zero")) == ["3"]
    assert ids(await repository.paginated_users(points="low", low_threshold=5)) == ["2", "3"]
    assert ids(
        await repository.paginated_users(protected=False, points="low", low_threshold=5)
    ) == ["2"]
    assert ids(await repository.paginated_users(sort="score_asc")) == ["3", "2", "1"]
    assert ids(await repository.paginated_users(sort="name")) == ["2", "3", "1"]

    scan_id = await repository.create_scan_run(period="7d", triggered_by="1", source="admin")
    assert await repository.fail_stale_scans("restarted") == 1
    stale = next(scan for scan in await repository.recent_scans() if scan["scan_id"] == scan_id)
    assert stale["status"] == "failed" and stale["error"] == "restarted"
    assert await repository.fail_stale_scans("restarted") == 0
