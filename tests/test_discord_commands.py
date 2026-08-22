from __future__ import annotations

from pathlib import Path

import pytest

from majors_lair_bot.discord_app import EngagementBot, EngagementCog
from majors_lair_bot.settings import Settings
from majors_lair_bot.sheets import GoogleSheetRepository
from majors_lair_bot.twitter_client import TwitterApiClient


@pytest.mark.asyncio
async def test_all_specified_slash_commands_register() -> None:
    settings = Settings(
        discord_token="unused",
        discord_guild_id=None,
        discord_audit_channel_id=None,
        admin_role_ids=frozenset(),
        twitter_api_key="unused",
        google_sheet_id="unused",
        google_service_account_file=Path("unused.json"),
        google_service_account_info=None,
        log_level="INFO",
    )
    repository = GoogleSheetRepository(
        sheet_id="unused", credentials_file=Path("unused.json"), credentials_info=None
    )
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
        "sync-sheet",
    }
    await bot.close()
