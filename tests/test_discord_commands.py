from __future__ import annotations

from typing import Any, cast

import pytest

from majors_lair_bot.discord_app import EngagementBot, EngagementCog
from majors_lair_bot.settings import Settings
from majors_lair_bot.twitter_client import TwitterApiClient


@pytest.mark.asyncio
async def test_all_specified_slash_commands_register() -> None:
    settings = Settings(
        discord_token="unused",
        discord_guild_id=123,
        discord_audit_channel_id=None,
        admin_role_ids=frozenset(),
        discord_client_id="client",
        discord_client_secret="secret",
        discord_oauth_redirect_uri="http://localhost:8000/auth/callback",
        twitter_api_key="unused",
        database_url="sqlite+aiosqlite:///:memory:",
        app_base_url="http://localhost:8000",
        admin_session_ttl_hours=12,
        session_cookie_secure=False,
        trusted_hosts=("localhost",),
        run_discord_bot=False,
        port=8000,
        log_level="INFO",
    )
    repository = cast(Any, object())
    twitter = TwitterApiClient("unused")
    bot = EngagementBot(settings=settings, repository=repository, twitter=twitter)
    await bot.add_cog(EngagementCog(bot))
    names = {command.name for command in bot.tree.get_commands()}
    assert names == {
        "link-twitter",
        "unlink-twitter",
        "leaderboard",
        "my-score",
        "my-history",
        "check-engagement",
        "refresh-engagement",
        "track-post",
        "low-activity-report",
        "reset-leaderboard",
        "sync-database",
    }
    await bot.close()
