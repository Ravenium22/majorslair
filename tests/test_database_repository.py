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


@pytest.mark.asyncio
async def test_windowed_leaderboard_sums_points_by_action_date(
    repository: DatabaseRepository,
) -> None:
    from datetime import UTC, datetime, timedelta

    from majors_lair_bot.models import ActionType, EngagementAction
    from majors_lair_bot.utils import isoformat

    await repository.link_user(
        discord_user_id="1", discord_username="old", twitter_handle="old", twitter_user_id="1"
    )
    await repository.link_user(
        discord_user_id="2", discord_username="new", twitter_handle="new", twitter_user_id="2"
    )
    now = datetime.now(UTC)

    def action(key: str, user: str, days_ago: int, points: float) -> EngagementAction:
        return EngagementAction(
            action_key=key,
            cycle_id="cycle_initial",
            discord_user_id=user,
            twitter_user_id=user,
            twitter_handle="h",
            action_type=ActionType.REPLY,
            target_handle="m_m3l",
            source_post_id="p",
            action_tweet_id=key,
            action_url="",
            text="hello there friend",
            normalized_text="hello there friend",
            content_hash=key,
            has_media=False,
            occurred_at=isoformat(now - timedelta(days=days_ago)),
            points=points,
            reason="test",
        )

    await repository.reconcile_actions(
        cycle_id="cycle_initial",
        discovered=[action("a", "1", 80, 10), action("b", "2", 5, 4), action("c", "1", 3, 1)],
        scopes=[],
    )

    month = await repository.leaderboard_window(
        cycle_id="cycle_initial", since=now - timedelta(days=30)
    )
    assert [(u.discord_user_id, u.score) for u in month] == [("2", 4.0), ("1", 1.0)]
    quarter = await repository.leaderboard_window(
        cycle_id="cycle_initial", since=now - timedelta(days=90)
    )
    assert [(u.discord_user_id, u.score) for u in quarter] == [("1", 11.0), ("2", 4.0)]


@pytest.mark.asyncio
async def test_reconcile_only_deactivates_tweets_x_confirms_gone(
    repository: DatabaseRepository,
) -> None:
    from datetime import UTC, datetime

    from majors_lair_bot.models import ActionType, EngagementAction, ReconcileScope
    from majors_lair_bot.utils import isoformat

    def action(key: str, tweet_id: str) -> EngagementAction:
        return EngagementAction(
            action_key=key,
            cycle_id="c",
            discord_user_id="1",
            twitter_user_id="1",
            twitter_handle="h",
            action_type=ActionType.REPLY,
            target_handle="m_m3l",
            source_post_id="p",
            action_tweet_id=tweet_id,
            action_url="",
            text="hello there",
            normalized_text="hello there",
            content_hash=key,
            has_media=False,
            occurred_at=isoformat(datetime.now(UTC)),
            points=5,
            reason="r",
        )

    await repository.reconcile_actions(
        cycle_id="c", discovered=[action("hidden", "11"), action("deleted", "22")], scopes=[]
    )
    scope = ReconcileScope(
        action_type=ActionType.REPLY, target_handle="m_m3l", source_post_id="p", complete=True
    )

    async def still_public(ids: list[str]) -> set[str]:
        assert sorted(ids) == ["11", "22"]
        return {"11"}  # X still serves the hidden reply; the other one is gone

    changed = await repository.reconcile_actions(
        cycle_id="c", discovered=[], scopes=[scope], still_public=still_public
    )
    assert changed == 1
    rows = {
        a.action_key: a.active
        for a in await repository.list_actions(cycle_id="c", include_inactive=True)
    }
    assert rows == {"hidden": True, "deleted": False}


@pytest.mark.asyncio
async def test_newcomers_get_a_grace_period_in_low_activity(
    repository: DatabaseRepository,
) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    await repository.register_member(
        discord_user_id="1", discord_username="fresh", discord_joined_at=now - timedelta(days=3)
    )
    await repository.register_member(
        discord_user_id="2", discord_username="old", discord_joined_at=now - timedelta(days=90)
    )
    await repository.register_member(discord_user_id="3", discord_username="unknown")

    ids = lambda users: sorted(u.discord_user_id for u in users)  # noqa: E731
    assert ids(await repository.low_activity(5)) == ["1", "2", "3"]
    assert ids(await repository.low_activity(5, grace_days=30)) == ["2", "3"]

    page = await repository.paginated_users(joined="new", grace_days=30)
    assert [i["discord_user_id"] for i in page["items"]] == ["1"]
    page = await repository.paginated_users(joined="established", grace_days=30)
    assert sorted(i["discord_user_id"] for i in page["items"]) == ["2", "3"]
    fresh = await repository.get_user("1")
    assert fresh is not None and fresh.discord_joined_at


@pytest.mark.asyncio
async def test_point_adjustments_and_transfers_survive_rescoring(
    repository: DatabaseRepository,
) -> None:
    from majors_lair_bot.database import DatabaseRepositoryError

    await repository.link_user(
        discord_user_id="1", discord_username="alice", twitter_handle="alice", twitter_user_id="1"
    )
    await repository.link_user(
        discord_user_id="2", discord_username="bob", twitter_handle="bob", twitter_user_id="2"
    )
    config = await repository.get_config()
    cycle = config["current_cycle_id"]

    rows = await repository.adjust_points(
        cycle_id=cycle, discord_user_id="1", points=10, reason="bonus", actor_discord_id="admin"
    )
    assert len(rows) == 1 and rows[0]["points"] == 10
    assert (await repository.get_user("1")).score == 10

    rows = await repository.adjust_points(
        cycle_id=cycle,
        discord_user_id="1",
        points=4,
        reason="move",
        actor_discord_id="admin",
        transfer_to="2",
    )
    assert [r["points"] for r in rows] == [-4, 4] and rows[0]["transfer_id"] == rows[1][
        "transfer_id"
    ]
    assert (await repository.get_user("1")).score == 6
    assert (await repository.get_user("2")).score == 4

    # A rescore recomputes from actions + adjustments; nothing is lost.
    await repository.save_scored_actions(cycle_id=cycle, scored_actions=[])
    assert (await repository.get_user("1")).score == 6
    assert (await repository.get_user("2")).score == 4

    history = await repository.list_adjustments("2")
    assert len(history) == 1 and history[0]["counterpart_discord_id"] == "1"

    with pytest.raises(DatabaseRepositoryError):
        await repository.adjust_points(
            cycle_id=cycle, discord_user_id="1", points=0, reason="", actor_discord_id="admin"
        )
    with pytest.raises(DatabaseRepositoryError):
        await repository.adjust_points(
            cycle_id=cycle,
            discord_user_id="1",
            points=1,
            reason="",
            actor_discord_id="admin",
            transfer_to="1",
        )

    assert (await repository.find_member("@BOB")).discord_user_id == "2"
    assert (await repository.find_member("2")).discord_user_id == "2"
    assert (await repository.find_member("alice")).discord_user_id == "1"
    assert await repository.find_member("nobody") is None


@pytest.mark.asyncio
async def test_low_activity_report_states_what_it_excluded(
    repository: DatabaseRepository,
) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    # Quiet, established, not protected: the only real candidate.
    await repository.register_member(
        discord_user_id="1", discord_username="quiet", discord_joined_at=now - timedelta(days=200)
    )
    # Protected, quiet: never listed.
    await repository.register_member(
        discord_user_id="2",
        discord_username="builder",
        special_role=True,
        discord_joined_at=now - timedelta(days=200),
    )
    # Joined last week: inside the grace period.
    await repository.register_member(
        discord_user_id="3", discord_username="fresh", discord_joined_at=now - timedelta(days=5)
    )
    # Join date unknown (never synced): treated as established, so it is listed.
    await repository.register_member(discord_user_id="4", discord_username="unknown")
    # Above the threshold.
    await repository.link_user(
        discord_user_id="5", discord_username="busy", twitter_handle="busy", twitter_user_id="9"
    )
    from majors_lair_bot.orm import UserRow

    async with repository.sessions.begin() as session:
        (await session.get(UserRow, "5")).score = 40

    report = await repository.low_activity_report(5, grace_days=30)

    assert [item["discord_user_id"] for item in report["items"]] == ["1", "4"]
    assert report["excluded_protected"] == 1
    assert report["excluded_newcomers"] == 1
    assert report["threshold"] == 5 and report["newcomer_grace_days"] == 30

    # Without a grace period the newcomer reappears; protection always applies.
    plain = await repository.low_activity_report(5, grace_days=0)
    assert sorted(item["discord_user_id"] for item in plain["items"]) == ["1", "3", "4"]
    assert plain["excluded_newcomers"] == 0 and plain["excluded_protected"] == 1


@pytest.mark.asyncio
async def test_audit_trail_is_searchable_by_member(repository: DatabaseRepository) -> None:
    await repository.register_member(discord_user_id="10", discord_username="alice")
    await repository.register_member(discord_user_id="20", discord_username="bob")
    await repository.append_audit(
        event_type="admin_points_adjusted", actor_discord_id="10", subject_discord_id="20"
    )
    await repository.append_audit(event_type="engagement_scan", actor_discord_id="10")

    by_name = await repository.paginated_audit(search="bob")
    assert [item["event_type"] for item in by_name["items"]] == ["admin_points_adjusted"]

    by_id = await repository.paginated_audit(search="20")
    assert by_id["total"] == 1

    by_type = await repository.paginated_audit(event_type="engagement_scan")
    assert by_type["total"] == 1
    assert "engagement_scan" in by_type["event_types"]
    assert "admin_points_adjusted" in by_type["event_types"]


@pytest.mark.asyncio
async def test_ensure_schema_refreshes_rule_descriptions_but_keeps_values(
    repository: DatabaseRepository,
) -> None:
    """Descriptions belong to the code. A database written before an explanation existed
    must pick it up on the next start, without losing what an admin set."""
    from sqlalchemy import update

    from majors_lair_bot.orm import ConfigRow

    placeholder = "Editable scoring or scan configuration."
    async with repository.sessions.begin() as session:
        await session.execute(update(ConfigRow).values(description=placeholder))
    await repository.set_config_values({"reply_primary": "9"}, actor_discord_id="42")

    await repository.ensure_schema()

    entries = {entry["key"]: entry for entry in await repository.list_config_entries()}
    assert not [e for e in entries.values() if e["description"] == placeholder]
    assert entries["reply_primary"]["value"] == "9"
    assert entries["reply_primary"]["updated_by"] == "42"


@pytest.mark.asyncio
async def test_actions_sort_orders_and_reject_unknown_values(
    repository: DatabaseRepository,
) -> None:
    from datetime import timedelta

    from majors_lair_bot.orm import ActionRow
    from majors_lair_bot.utils import utc_now

    now = utc_now()
    cycle = (await repository.get_config())["current_cycle_id"]
    await repository.link_user(
        discord_user_id="1", discord_username="alice", twitter_handle="zzz_a", twitter_user_id="t1"
    )
    await repository.link_user(
        discord_user_id="2", discord_username="bob", twitter_handle="aaa_b", twitter_user_id="t2"
    )
    async with repository.sessions.begin() as session:
        for index, (user_id, handle, points, when) in enumerate(
            [("1", "zzz_a", 3.0, now - timedelta(days=2)), ("2", "aaa_b", 9.0, now - timedelta(days=1))]
        ):
            session.add(
                ActionRow(
                    action_key=f"k{index}",
                    cycle_id=cycle,
                    discord_user_id=user_id,
                    twitter_user_id=f"t{user_id}",
                    twitter_handle=handle,
                    action_type="reply",
                    target_handle="major",
                    source_post_id="p",
                    action_tweet_id=f"a{index}",
                    action_url="",
                    text="hi",
                    normalized_text="hi",
                    content_hash=f"h{index}",
                    has_media=False,
                    occurred_at=when,
                    points=points,
                    reason="r",
                    active=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

    newest = await repository.paginated_actions(sort="occurred_desc")
    assert [item["discord_username"] for item in newest["items"]] == ["bob", "alice"]

    cheapest = await repository.paginated_actions(sort="points_asc")
    assert [item["points"] for item in cheapest["items"]] == [3.0, 9.0]

    by_member = await repository.paginated_actions(sort="member")
    assert [item["twitter_handle"] for item in by_member["items"]] == ["aaa_b", "zzz_a"]

    unknown = await repository.paginated_actions(sort="'; drop table actions; --")
    assert unknown["sort"] == "occurred_desc"
    assert [item["discord_username"] for item in unknown["items"]] == ["bob", "alice"]


@pytest.mark.asyncio
async def test_delete_member_erases_their_data_but_not_frozen_history(
    repository: DatabaseRepository,
) -> None:
    from majors_lair_bot.orm import ActionRow
    from majors_lair_bot.utils import utc_now

    now = utc_now()
    cycle = (await repository.get_config())["current_cycle_id"]
    await repository.link_user(
        discord_user_id="900", discord_username="alt", twitter_handle="alt_x", twitter_user_id="t9"
    )
    await repository.link_user(
        discord_user_id="901", discord_username="keeper", twitter_handle="k_x", twitter_user_id="t8"
    )
    async with repository.sessions.begin() as session:
        for index, owner in enumerate(["900", "900", "901"]):
            session.add(
                ActionRow(
                    action_key=f"d{index}",
                    cycle_id=cycle,
                    discord_user_id=owner,
                    twitter_user_id="t9",
                    twitter_handle="alt_x",
                    action_type="reply",
                    target_handle="major",
                    source_post_id="p",
                    action_tweet_id=f"t{index}",
                    action_url="",
                    text="hi",
                    normalized_text="hi",
                    content_hash=f"c{index}",
                    has_media=False,
                    occurred_at=now,
                    points=2.0,
                    reason="r",
                    active=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
    await repository.adjust_points(
        discord_user_id="900", points=5, reason="test", actor_discord_id="1", cycle_id=cycle
    )
    # Freeze a cycle, so we can prove the delete does not rewrite closed history.
    await repository.reset_leaderboard(actor_discord_id="1")
    frozen_before = await repository.list_snapshots()

    removed = await repository.delete_member("900")

    assert removed is not None
    assert removed["discord_username"] == "alt"
    assert removed["actions"] == 2
    assert removed["adjustments"] == 1
    assert await repository.get_user("900") is None
    assert await repository.get_user("901") is not None
    remaining = await repository.paginated_actions(page_size=50)
    assert {item["discord_user_id"] for item in remaining["items"]} <= {"901"}
    assert await repository.list_adjustments("900") == []
    assert await repository.list_snapshots() == frozen_before
    assert await repository.delete_member("900") is None


@pytest.mark.asyncio
async def test_deactivate_members_only_touches_the_active_ones(
    repository: DatabaseRepository,
) -> None:
    await repository.link_user(
        discord_user_id="910", discord_username="gone", twitter_handle="g_x", twitter_user_id="t1"
    )
    await repository.link_user(
        discord_user_id="911", discord_username="stays", twitter_handle="s_x", twitter_user_id="t2"
    )
    await repository.set_user_active("911", False)

    changed = await repository.deactivate_members({"910", "911", "does-not-exist"})

    assert [row["discord_username"] for row in changed] == ["gone"]
    gone = await repository.get_user("910")
    assert gone is not None and gone.active is False
    assert await repository.deactivate_members(set()) == []
