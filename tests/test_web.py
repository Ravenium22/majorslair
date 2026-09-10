from __future__ import annotations

import httpx
import pytest

from majors_lair_bot.settings import Settings
from majors_lair_bot.web import create_app


@pytest.mark.asyncio
async def test_health_endpoint_starts_without_discord_gateway() -> None:
    settings = Settings(
        discord_token="unused",
        discord_guild_id=123,
        discord_audit_channel_id=None,
        admin_role_ids=frozenset({456}),
        discord_client_id="client",
        discord_client_secret="secret",
        discord_oauth_redirect_uri="http://localhost:8000/auth/callback",
        twitter_api_key="unused",
        database_url="sqlite+aiosqlite:///:memory:",
        app_base_url="http://localhost:8000",
        admin_session_ttl_hours=12,
        session_cookie_secure=False,
        trusted_hosts=("testserver",),
        run_discord_bot=False,
        port=8000,
        log_level="INFO",
    )

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
