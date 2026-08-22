from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    discord_guild_id: int | None
    discord_audit_channel_id: int | None
    admin_role_ids: frozenset[int]
    twitter_api_key: str
    google_sheet_id: str
    google_service_account_file: Path | None
    google_service_account_info: dict[str, object] | None
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        required = {
            "DISCORD_TOKEN": os.getenv("DISCORD_TOKEN", "").strip(),
            "TWITTERAPI_IO_KEY": os.getenv("TWITTERAPI_IO_KEY", "").strip(),
            "GOOGLE_SHEET_ID": os.getenv("GOOGLE_SHEET_ID", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SettingsError(f"Missing required environment variables: {', '.join(missing)}")

        credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        credentials_info: dict[str, object] | None = None
        if credentials_json:
            try:
                parsed = json.loads(credentials_json)
            except json.JSONDecodeError as exc:
                raise SettingsError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise SettingsError("GOOGLE_SERVICE_ACCOUNT_JSON must contain a JSON object")
            credentials_info = parsed

        credentials_path = Path(credentials_file).expanduser() if credentials_file else None
        if credentials_info is None and credentials_path is None:
            raise SettingsError("Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON")

        return cls(
            discord_token=required["DISCORD_TOKEN"],
            discord_guild_id=_optional_int("DISCORD_GUILD_ID"),
            discord_audit_channel_id=_optional_int("DISCORD_AUDIT_CHANNEL_ID"),
            admin_role_ids=_int_set("ADMIN_ROLE_IDS"),
            twitter_api_key=required["TWITTERAPI_IO_KEY"],
            google_sheet_id=required["GOOGLE_SHEET_ID"],
            google_service_account_file=credentials_path,
            google_service_account_info=credentials_info,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
