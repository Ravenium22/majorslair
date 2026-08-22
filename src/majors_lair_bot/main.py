from __future__ import annotations

import logging

from .discord_app import EngagementBot
from .settings import Settings, SettingsError
from .sheets import GoogleSheetRepository
from .twitter_client import TwitterApiClient


def main() -> None:
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    repository = GoogleSheetRepository(
        sheet_id=settings.google_sheet_id,
        credentials_file=settings.google_service_account_file,
        credentials_info=settings.google_service_account_info,
    )
    twitter = TwitterApiClient(settings.twitter_api_key)
    bot = EngagementBot(settings=settings, repository=repository, twitter=twitter)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
