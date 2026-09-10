from __future__ import annotations

import pytest

from majors_lair_bot.settings import Settings, SettingsError

BASE_ENV = {
    "DISCORD_TOKEN": "t",
    "DISCORD_GUILD_ID": "1",
    "DISCORD_CLIENT_ID": "c",
    "DISCORD_CLIENT_SECRET": "s",
    "TWITTERAPI_IO_KEY": "k",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
}


def _apply(monkeypatch: pytest.MonkeyPatch, extra: dict[str, str]) -> None:
    for name in (
        "DOMAIN",
        "APP_BASE_URL",
        "DISCORD_OAUTH_REDIRECT_URI",
        "TRUSTED_HOSTS",
        *BASE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**BASE_ENV, **extra}.items():
        monkeypatch.setenv(name, value)


def test_domain_alone_derives_public_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {"DOMAIN": "lair.duckdns.org"})
    settings = Settings.from_env()
    assert settings.app_base_url == "https://lair.duckdns.org"
    assert settings.discord_oauth_redirect_uri == "https://lair.duckdns.org/auth/callback"
    assert "lair.duckdns.org" in settings.trusted_hosts
    assert "localhost" in settings.trusted_hosts
    assert settings.session_cookie_secure is True


def test_explicit_values_win_over_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(
        monkeypatch,
        {
            "DOMAIN": "https://lair.duckdns.org/",
            "APP_BASE_URL": "http://localhost:8000/",
            "DISCORD_OAUTH_REDIRECT_URI": "http://localhost:8000/custom",
            "TRUSTED_HOSTS": "a.example, b.example",
        },
    )
    settings = Settings.from_env()
    assert settings.app_base_url == "http://localhost:8000"
    assert settings.discord_oauth_redirect_uri == "http://localhost:8000/custom"
    assert settings.trusted_hosts == ("a.example", "b.example")
    assert settings.session_cookie_secure is False


def test_missing_domain_and_base_url_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {})
    with pytest.raises(SettingsError, match="DOMAIN"):
        Settings.from_env()
