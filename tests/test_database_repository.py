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
