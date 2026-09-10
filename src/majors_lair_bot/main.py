from __future__ import annotations

import asyncio
import logging
import sys

import httpx
import uvicorn
from sqlalchemy import text

from .database import create_database_engine
from .settings import Settings, SettingsError
from .twitter_client import TwitterApiClient, TwitterApiError
from .web import DISCORD_API, create_app

LOGGER = logging.getLogger(__name__)

USAGE = """Usage:
  majors-lair-bot          Run the web console and Discord bot.
  majors-lair-bot check    Test the configuration (database, Discord, twitterapi.io) and exit.
"""


class PreflightError(RuntimeError):
    """A configuration problem explained in plain language."""


async def _check_database(settings: Settings) -> None:
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise PreflightError(
            f"Cannot connect to the database: {exc}\n"
            "  On the Hetzner setup the database is the 'db' container: run "
            "'majorbot status' and make sure POSTGRES_PASSWORD in .env was not changed "
            "after the first start."
        ) from exc
    finally:
        await engine.dispose()


async def _check_discord(settings: Settings) -> str:
    headers = {"Authorization": f"Bot {settings.discord_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            me = await client.get(f"{DISCORD_API}/users/@me", headers=headers)
        except httpx.HTTPError as exc:
            raise PreflightError(f"Could not reach Discord: {exc}") from exc
        if me.status_code == 401:
            raise PreflightError(
                "Discord rejected DISCORD_TOKEN. Copy a fresh token from the Discord "
                "Developer Portal > your application > Bot > Reset Token, paste it into .env."
            )
        if me.status_code >= 400:
            raise PreflightError(f"Discord token check failed ({me.status_code}): {me.text[:200]}")
        guild = await client.get(
            f"{DISCORD_API}/guilds/{settings.discord_guild_id}", headers=headers
        )
        if guild.status_code in {403, 404}:
            raise PreflightError(
                f"The bot is not in the Discord server with ID {settings.discord_guild_id}.\n"
                "  Check DISCORD_GUILD_ID, and invite the bot with the OAuth2 URL from the "
                "Developer Portal (scopes: bot, applications.commands)."
            )
    return str(me.json().get("username", "bot"))


async def _check_twitter(twitter: TwitterApiClient, handle: str) -> None:
    try:
        async with twitter:
            await twitter.get_user_info(handle)
    except TwitterApiError as exc:
        if exc.status == 401:
            raise PreflightError(
                "twitterapi.io rejected the API key. Check TWITTERAPI_IO_KEY in .env "
                "(copy it from https://twitterapi.io/dashboard)."
            ) from exc
        if exc.status == 402:
            raise PreflightError(
                "twitterapi.io says the account balance is empty. Add credit at "
                "https://twitterapi.io/dashboard."
            ) from exc
        raise PreflightError(f"twitterapi.io check failed: {exc}") from exc


def preflight(settings: Settings) -> None:
    """Validate every external dependency; raise PreflightError with a readable message."""
    print("Checking database connection...", flush=True)
    asyncio.run(_check_database(settings))
    print("  OK: database reachable.")

    print("Checking Discord token and server...", flush=True)
    name = asyncio.run(_check_discord(settings))
    print(f"  OK: signed in to Discord as {name}, server {settings.discord_guild_id} visible.")

    print("Checking twitterapi.io key...", flush=True)
    asyncio.run(_check_twitter(TwitterApiClient(settings.twitter_api_key), "m_m3l"))
    print("  OK: twitterapi.io responded.")

    print("Admin website settings:")
    print(f"  Public address:      {settings.app_base_url}")
    print(f"  OAuth redirect URI:  {settings.discord_oauth_redirect_uri}")
    print(
        "  Make sure that exact redirect URI is listed under OAuth2 > Redirects in the "
        "Discord Developer Portal, otherwise the admin login will fail."
    )
    if not settings.discord_oauth_redirect_uri.startswith(settings.app_base_url):
        print(
            "  WARNING: the redirect URI does not start with the public address; "
            "the login will probably fail."
        )


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return
    check_only = bool(args) and args[0] == "check"
    if args and not check_only:
        print(USAGE)
        raise SystemExit(2)

    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        raise SystemExit(
            f"Configuration error: {exc}\n"
            "Open the .env file and fill in the missing value (see docs/HETZNER_GUIDE.md)."
        ) from exc

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        preflight(settings)
    except PreflightError as exc:
        raise SystemExit(f"Configuration problem:\n  {exc}") from exc

    if check_only:
        print("Everything looks good. Start the bot now.")
        return

    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
