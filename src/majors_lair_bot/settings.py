from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class SettingsError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc


def _int_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    try:
        return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise SettingsError(f"{name} must be a comma-separated list of integers") from exc


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SettingsError(f"Missing required environment variable: {name}")
    return value


def _bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false")


def _positive_int(name: str, *, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value <= 0:
        raise SettingsError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    discord_guild_id: int
    discord_audit_channel_id: int | None
    admin_role_ids: frozenset[int]
    discord_client_id: str
    discord_client_secret: str
    discord_oauth_redirect_uri: str
    twitter_api_key: str
    database_url: str
    app_base_url: str
    admin_session_ttl_hours: int
    session_cookie_secure: bool
    trusted_hosts: tuple[str, ...]
    run_discord_bot: bool
    port: int
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        guild_id = _optional_int("DISCORD_GUILD_ID")
        if guild_id is None:
            raise SettingsError("Missing required environment variable: DISCORD_GUILD_ID")
        app_base_url = _required("APP_BASE_URL").rstrip("/")
        redirect_uri = _required("DISCORD_OAUTH_REDIRECT_URI")
        hosts = tuple(
            host.strip()
            for host in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1").split(",")
            if host.strip()
        )

        return cls(
            discord_token=_required("DISCORD_TOKEN"),
            discord_guild_id=guild_id,
            discord_audit_channel_id=_optional_int("DISCORD_AUDIT_CHANNEL_ID"),
            admin_role_ids=_int_set("ADMIN_ROLE_IDS"),
            discord_client_id=_required("DISCORD_CLIENT_ID"),
            discord_client_secret=_required("DISCORD_CLIENT_SECRET"),
            discord_oauth_redirect_uri=redirect_uri,
            twitter_api_key=_required("TWITTERAPI_IO_KEY"),
            database_url=_required("DATABASE_URL"),
            app_base_url=app_base_url,
            admin_session_ttl_hours=_positive_int("ADMIN_SESSION_TTL_HOURS", default=12),
            session_cookie_secure=_bool(
                "SESSION_COOKIE_SECURE", default=app_base_url.startswith("https://")
            ),
            trusted_hosts=hosts,
            run_discord_bot=_bool("RUN_DISCORD_BOT", default=True),
            port=_positive_int("PORT", default=8000),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
