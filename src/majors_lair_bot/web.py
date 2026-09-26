from __future__ import annotations

import asyncio
import csv
import io
import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .database import (
    DatabaseRepository,
    DatabaseRepositoryError,
    LinkConflictError,
    create_database_engine,
)
from .discord_app import EngagementBot
from .engagement import EngagementService
from .roles import match_protected_roles, split_setting
from .scoring import DEFAULT_CONFIG, ScoringRules
from .settings import Settings
from .twitter_client import TwitterApiClient, TwitterApiError
from .utils import parse_bool, parse_datetime, parse_period, utc_now

LOGGER = logging.getLogger(__name__)
DISCORD_API = "https://discord.com/api/v10"
SESSION_COOKIE = "majors_lair_session"
OAUTH_STATE_COOKIE = "majors_lair_oauth_state"
ADMINISTRATOR_PERMISSION = 1 << 3
# Discord role membership is re-checked at most this often per signed-in admin. The
# dashboard polls every 10 seconds, so without a cache every poll cost three Discord calls.
ACCESS_CACHE_SECONDS = 60


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = DatabaseRepository(create_database_engine(settings.database_url))
        self.twitter = TwitterApiClient(settings.twitter_api_key)
        self.service = EngagementService(self.repository, self.twitter)
        self.http = httpx.AsyncClient(timeout=15)
        self.bot: EngagementBot | None = None
        self.bot_task: asyncio.Task[None] | None = None
        self.scan_task: asyncio.Task[None] | None = None
        self.access_cache: dict[str, tuple[float, list[str], bool]] = {}

    async def start(self) -> None:
        await self.repository.ensure_schema()
        stale = await self.repository.fail_stale_scans(
            "Interrupted: the bot restarted (deploy or crash) while this scan was running. "
            "Nothing was scored; run it again."
        )
        if stale:
            LOGGER.warning("Marked %s interrupted scan(s) as failed after restart", stale)
        await self.twitter.start()
        if self.settings.run_discord_bot:
            self.bot = EngagementBot(
                settings=self.settings,
                repository=self.repository,
                twitter=self.twitter,
            )
            self.bot_task = asyncio.create_task(
                self.bot.start(self.settings.discord_token),
                name="discord-bot",
            )
            self.bot_task.add_done_callback(self._log_bot_exit)

    @staticmethod
    def _log_bot_exit(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error:
            LOGGER.error("Discord bot stopped unexpectedly", exc_info=error)

    async def close(self) -> None:
        if self.scan_task and not self.scan_task.done():
            self.scan_task.cancel()
            await asyncio.gather(self.scan_task, return_exceptions=True)
        if self.bot:
            await self.bot.close()
        if self.bot_task:
            await asyncio.gather(self.bot_task, return_exceptions=True)
        await self.twitter.close()
        await self.http.aclose()
        await self.repository.close()


class ActiveToggle(BaseModel):
    active: bool


class UserUpdate(BaseModel):
    active: bool | None = None
    discord_username: str | None = Field(default=None, min_length=1, max_length=120)
    special_role: bool | None = None
    special_role_names: str | None = Field(default=None, max_length=255)


class LinkUserRequest(BaseModel):
    discord_user_id: str = Field(min_length=5, max_length=32, pattern=r"^\d+$")
    discord_username: str = Field(min_length=1, max_length=120)
    twitter_handle: str = Field(min_length=1, max_length=32)


class ImportRow(BaseModel):
    discord_user_id: str = Field(min_length=5, max_length=32, pattern=r"^\d+$")
    discord_username: str = Field(min_length=1, max_length=120)
    twitter_handle: str = Field(default="", max_length=64)
    special_role: bool | None = None
    special_role_names: str | None = Field(default=None, max_length=255)


class ImportRequest(BaseModel):
    rows: list[ImportRow] = Field(min_length=1, max_length=500)


class VerifyRequest(BaseModel):
    skip_protected: bool = False


class MemberFilters(BaseModel):
    search: str = ""
    active: bool | None = True
    protected: bool | None = None
    linked: bool | None = None
    x_ok: bool | None = None
    points: str = "any"
    joined: str = "any"
    min_score: float | None = None
    max_score: float | None = None
    threshold: float | None = None
    follows: str = "any"


class RoleBulkRequest(BaseModel):
    role_id: str = Field(min_length=5, max_length=32, pattern=r"^\d+$")
    action: str = Field(default="add", pattern=r"^(add|remove)$")
    filters: MemberFilters = Field(default_factory=MemberFilters)
    dry_run: bool = False
    # An explicit list of members, used instead of `filters` when the admin ticked specific
    # rows. Naming three people should not require building a filter that matches only them.
    discord_user_ids: list[str] = Field(default_factory=list, max_length=2000)
    # Members who currently hold any of these roles are left alone (checked live).
    exclude_role_ids: list[str] = Field(default_factory=list)


class AdjustRequest(BaseModel):
    points: float = Field(gt=-100000, lt=100000)
    reason: str = Field(default="", max_length=300)
    transfer_to: str | None = Field(default=None, max_length=120)


class MemberScanRequest(BaseModel):
    period: str = Field(default="30d", min_length=2, max_length=10)
    max_pages: int = Field(default=25, ge=1, le=250)


class DiagnoseRequest(BaseModel):
    url: str = Field(min_length=5, max_length=300)


class ScanRequest(BaseModel):
    period: str = Field(default="24h", min_length=2, max_length=10)
    verify_x: bool = True
    skip_protected: bool | None = None
    read_timelines: bool | None = None
    timeline_pages: int | None = Field(default=None, ge=1, le=250)


class TrackPostRequest(BaseModel):
    url: str = Field(min_length=15, max_length=300)


class ConfigUpdate(BaseModel):
    values: dict[str, str]


class ResetRequest(BaseModel):
    confirmation: str


def _runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime


async def _discord_json(
    runtime: AppRuntime,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> Any:
    response = await runtime.http.request(
        method, f"{DISCORD_API}{path}", headers=headers, data=data
    )
    if response.status_code >= 400:
        LOGGER.warning("Discord API %s returned %s", path, response.status_code)
        raise HTTPException(status_code=403, detail="Discord authorization could not be verified")
    return response.json()


def _avatar_url(user: dict[str, Any]) -> str:
    avatar = user.get("avatar")
    if not avatar:
        return ""
    return f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar}.png?size=128"


async def _current_discord_access(
    runtime: AppRuntime, user_id: str, *, use_cache: bool = True
) -> tuple[list[str], bool]:
    cached = runtime.access_cache.get(user_id)
    if use_cache and cached and cached[0] > time.monotonic():
        return cached[1], cached[2]
    headers = {"Authorization": f"Bot {runtime.settings.discord_token}"}
    guild_id = runtime.settings.discord_guild_id
    member_path = f"/guilds/{guild_id}/members/{user_id}"
    member, guild, roles = await asyncio.gather(
        _discord_json(runtime, "GET", member_path, headers=headers),
        _discord_json(runtime, "GET", f"/guilds/{guild_id}", headers=headers),
        _discord_json(runtime, "GET", f"/guilds/{guild_id}/roles", headers=headers),
    )
    role_ids = [str(role_id) for role_id in member.get("roles", [])]
    role_map = {str(role["id"]): int(role.get("permissions", "0")) for role in roles}
    is_owner = str(guild.get("owner_id", "")) == user_id
    is_administrator = any(
        role_map.get(role_id, 0) & ADMINISTRATOR_PERMISSION for role_id in role_ids
    )
    configured = bool(runtime.settings.admin_role_ids.intersection(map(int, role_ids)))
    authorized = bool(is_owner or is_administrator or configured)
    runtime.access_cache[user_id] = (
        time.monotonic() + ACCESS_CACHE_SECONDS,
        role_ids,
        authorized,
    )
    return role_ids, authorized


async def require_admin(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Sign in with Discord")
    session = await runtime.repository.get_admin_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Session expired")
    role_ids, authorized = await _current_discord_access(runtime, str(session["discord_user_id"]))
    if not authorized:
        await runtime.repository.delete_admin_session(token)
        raise HTTPException(status_code=403, detail="Engagement admin access required")
    session["role_ids"] = role_ids
    session["is_guild_admin"] = authorized
    return session


Admin = Annotated[dict[str, Any], Depends(require_admin)]


def require_csrf(request: Request, admin: Admin) -> dict[str, Any]:
    supplied = request.headers.get("x-csrf-token", "")
    expected = str(admin["csrf_token"])
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return admin


MutatingAdmin = Annotated[dict[str, Any], Depends(require_csrf)]


def _page(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), max(1, min(100, page_size))


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    runtime = AppRuntime(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="Major's Lair Engagement Control",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(resolved.trusted_hosts))

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/login")
    async def login() -> RedirectResponse:
        state = secrets.token_urlsafe(32)
        query = urlencode(
            {
                "client_id": resolved.discord_client_id,
                "response_type": "code",
                "redirect_uri": resolved.discord_oauth_redirect_uri,
                "scope": "identify guilds guilds.members.read",
                "state": state,
            }
        )
        response = RedirectResponse(f"https://discord.com/oauth2/authorize?{query}")
        response.set_cookie(
            OAUTH_STATE_COOKIE,
            state,
            max_age=600,
            httponly=True,
            secure=resolved.session_cookie_secure,
            samesite="lax",
        )
        return response

    @app.get("/auth/callback")
    async def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
        expected = request.cookies.get(OAUTH_STATE_COOKIE, "")
        if not code or not state or not expected or not secrets.compare_digest(state, expected):
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        token = await _discord_json(
            runtime,
            "POST",
            "/oauth2/token",
            data={
                "client_id": resolved.discord_client_id,
                "client_secret": resolved.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": resolved.discord_oauth_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        bearer = {"Authorization": f"Bearer {token['access_token']}"}
        user, member = await asyncio.gather(
            _discord_json(runtime, "GET", "/users/@me", headers=bearer),
            _discord_json(
                runtime,
                "GET",
                f"/users/@me/guilds/{resolved.discord_guild_id}/member",
                headers=bearer,
            ),
        )
        role_ids, authorized = await _current_discord_access(
            runtime, str(user["id"]), use_cache=False
        )
        if not authorized:
            raise HTTPException(status_code=403, detail="Engagement admin access required")
        username = (
            member.get("nick") or user.get("global_name") or user.get("username") or user["id"]
        )
        session_token, _ = await runtime.repository.create_admin_session(
            discord_user_id=str(user["id"]),
            discord_username=str(username),
            avatar_url=_avatar_url(user),
            role_ids=role_ids,
            is_guild_admin=authorized,
            ttl_hours=resolved.admin_session_ttl_hours,
        )
        await runtime.repository.append_audit(
            event_type="admin_login", actor_discord_id=str(user["id"])
        )
        response = RedirectResponse(resolved.app_base_url, status_code=303)
        response.delete_cookie(OAUTH_STATE_COOKIE)
        response.set_cookie(
            SESSION_COOKIE,
            session_token,
            max_age=resolved.admin_session_ttl_hours * 3600,
            httponly=True,
            secure=resolved.session_cookie_secure,
            samesite="lax",
        )
        return response

    @app.get("/api/session")
    async def session(admin: Admin) -> dict[str, Any]:
        return {
            "user": {
                "id": admin["discord_user_id"],
                "username": admin["discord_username"],
                "avatar_url": admin["avatar_url"],
            },
            "csrf_token": admin["csrf_token"],
            "expires_at": admin["expires_at"],
        }

    @app.post("/api/logout")
    async def logout(request: Request, admin: MutatingAdmin) -> Response:
        await runtime.repository.delete_admin_session(request.cookies.get(SESSION_COOKIE, ""))
        runtime.access_cache.pop(str(admin["discord_user_id"]), None)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/api/overview")
    async def overview(_: Admin) -> dict[str, Any]:
        overview_data, leaders, scans = await asyncio.gather(
            runtime.repository.overview(),
            runtime.repository.leaderboard(8),
            runtime.repository.recent_scans(6),
        )
        overview_data["leaderboard"] = [asdict(user) for user in leaders]
        overview_data["recent_scans"] = scans
        overview_data["bot_connected"] = bool(runtime.bot and runtime.bot.is_ready())
        return overview_data

    @app.get("/api/leaderboard")
    async def leaderboard(
        _: Admin, window: str = "cycle", limit: int = Query(default=25, ge=1, le=500)
    ) -> dict[str, Any]:
        """Top members for the whole cycle or for a trailing window such as 30d or 90d."""
        if window in {"cycle", "all", ""}:
            users_list = await runtime.repository.leaderboard(limit)
            return {"window": "cycle", "items": [asdict(user) for user in users_list]}
        try:
            duration, label = parse_period(window)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        config_values = await runtime.repository.get_config()
        cycle_id = config_values.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        users_list = await runtime.repository.leaderboard_window(
            cycle_id=cycle_id, since=utc_now() - duration, limit=limit
        )
        return {"window": label, "items": [asdict(user) for user in users_list]}

    @app.get("/api/users")
    async def users(
        _: Admin,
        search: str = "",
        active: bool | None = None,
        protected: bool | None = None,
        linked: bool | None = None,
        x_ok: bool | None = None,
        points: str = "any",
        sort: str = "score_desc",
        joined: str = "any",
        min_score: float | None = None,
        max_score: float | None = None,
        threshold: float | None = None,
        follows: str = "any",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        config_values = await runtime.repository.get_config()
        if points == "low":
            threshold = (
                threshold
                if threshold is not None
                else float(config_values["low_activity_threshold"])
            )
        else:
            threshold = None
        grace = int(config_values.get("newcomer_grace_days", DEFAULT_CONFIG["newcomer_grace_days"]))
        return await runtime.repository.paginated_users(
            search=search,
            active=active,
            protected=protected,
            linked=linked,
            x_ok=x_ok,
            points=points,
            low_threshold=threshold,
            sort=sort,
            joined=joined,
            grace_days=grace,
            min_score=min_score,
            max_score=max_score,
            follows=follows,
            page=page,
            page_size=page_size,
        )

    async def _fetch_guild_members() -> list[dict[str, Any]]:
        """Every member of the server, with their roles, straight from Discord.

        One request per 1000 members. Raises rather than returning a partial list, because
        callers use this to decide who is protected and a short list would silently mean
        "nobody is protected".
        """
        headers = {"Authorization": f"Bot {runtime.settings.discord_token}"}
        guild_id = runtime.settings.discord_guild_id
        members: list[dict[str, Any]] = []
        after = "0"
        for _ in range(100):  # 100 pages x 1000 members is far beyond this community
            response = await runtime.http.get(
                f"{DISCORD_API}/guilds/{guild_id}/members",
                headers=headers,
                params={"limit": 1000, "after": after},
            )
            if response.status_code == 429:
                retry = float(response.headers.get("Retry-After", "1") or 1)
                await asyncio.sleep(min(10.0, retry))
                continue
            if response.status_code == 403:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Discord refused the member list. Enable 'Server Members Intent' "
                        "under Bot > Privileged Gateway Intents in the Developer Portal, "
                        "then try again."
                    ),
                )
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Discord member list failed ({response.status_code})",
                )
            page = response.json()
            if not isinstance(page, list) or not page:
                break
            members.extend(item for item in page if isinstance(item, dict))
            after = str(page[-1].get("user", {}).get("id", ""))
            if len(page) < 1000 or not after:
                break
        return members

    async def _members_matching(filters: MemberFilters) -> list[dict[str, Any]]:
        config_values = await runtime.repository.get_config()
        threshold = None
        if filters.points == "low":
            threshold = (
                filters.threshold
                if filters.threshold is not None
                else float(config_values["low_activity_threshold"])
            )
        grace = int(config_values.get("newcomer_grace_days", DEFAULT_CONFIG["newcomer_grace_days"]))
        page = await runtime.repository.paginated_users(
            search=filters.search,
            active=filters.active,
            protected=filters.protected,
            linked=filters.linked,
            x_ok=filters.x_ok,
            points=filters.points,
            low_threshold=threshold,
            joined=filters.joined,
            grace_days=grace,
            min_score=filters.min_score,
            max_score=filters.max_score,
            follows=filters.follows,
            page=1,
            page_size=10000,
        )
        return page["items"]

    @app.get("/api/discord/roles")
    async def discord_roles(_: Admin) -> dict[str, Any]:
        """Assignable roles of the server, plus whether the bot can manage them."""
        headers = {"Authorization": f"Bot {runtime.settings.discord_token}"}
        guild_id = runtime.settings.discord_guild_id
        roles_response = await runtime.http.get(
            f"{DISCORD_API}/guilds/{guild_id}/roles", headers=headers
        )
        if roles_response.status_code >= 400:
            raise HTTPException(status_code=502, detail="Could not read the server's roles")
        me = await runtime.http.get(f"{DISCORD_API}/users/@me", headers=headers)
        bot_id = str(me.json().get("id", "")) if me.status_code < 400 else ""
        bot_member = await runtime.http.get(
            f"{DISCORD_API}/guilds/{guild_id}/members/{bot_id}", headers=headers
        )
        bot_role_ids = (
            {str(r) for r in bot_member.json().get("roles", [])}
            if bot_member.status_code < 400
            else set()
        )
        roles = [r for r in roles_response.json() if isinstance(r, dict)]
        bot_top = max(
            (int(r.get("position", 0)) for r in roles if str(r["id"]) in bot_role_ids), default=0
        )
        manage_roles = any(
            (int(r.get("permissions", 0)) & 0x10000000) or (int(r.get("permissions", 0)) & 0x8)
            for r in roles
            if str(r["id"]) in bot_role_ids
        )
        output = [
            {
                "id": str(r["id"]),
                "name": r.get("name", ""),
                "color": r.get("color", 0),
                "position": int(r.get("position", 0)),
                "managed": bool(r.get("managed")),
                # Discord marks the Nitro booster role with a premium_subscriber tag.
                "booster": "premium_subscriber" in (r.get("tags") or {}),
                "assignable": manage_roles
                and not r.get("managed")
                and int(r.get("position", 0)) < bot_top
                and str(r["id"]) != str(guild_id),
            }
            for r in roles
            if str(r["id"]) != str(guild_id)
        ]
        output.sort(key=lambda r: -r["position"])
        return {"roles": output, "bot_can_manage_roles": manage_roles, "bot_top_position": bot_top}

    @app.post("/api/users/roles")
    async def bulk_role(payload: RoleBulkRequest, admin: MutatingAdmin) -> dict[str, Any]:
        """Add or remove one Discord role for the chosen members, or for everyone matching
        the filters when no explicit list was given."""
        if payload.discord_user_ids:
            wanted = {str(value) for value in payload.discord_user_ids}
            members = [m for m in await _members_matching(MemberFilters()) if m["discord_user_id"] in wanted]
        else:
            members = await _members_matching(payload.filters)
        headers = {"Authorization": f"Bot {runtime.settings.discord_token}"}
        guild_id = runtime.settings.discord_guild_id
        excluded_ids = {str(r) for r in payload.exclude_role_ids if str(r).isdigit()}
        skipped: list[dict[str, str]] = []
        semaphore = asyncio.Semaphore(4)

        unverified: list[dict[str, str]] = []
        if excluded_ids:
            # Read everyone's roles right now, so a role given after the preview still counts.
            #
            # This used to ask Discord about each member separately and treat any failed
            # request as "holds no roles". A rate limit therefore stripped someone's
            # protection in silence, which is how a member with the excluded role ended up in
            # the list. One member-list call replaces all of those requests, and anyone whose
            # roles still cannot be read is left out of the action rather than swept into it.
            names_response = await runtime.http.get(
                f"{DISCORD_API}/guilds/{guild_id}/roles", headers=headers
            )
            role_names = (
                {str(r["id"]): str(r.get("name", "")) for r in names_response.json()}
                if names_response.status_code < 400
                else {}
            )
            held_by: dict[str, set[str]] = {}
            for entry in await _fetch_guild_members():
                member_id = str((entry.get("user") or {}).get("id") or "")
                if member_id:
                    held_by[member_id] = {str(r) for r in entry.get("roles", [])}

            async def current_roles(member_id: str) -> set[str] | None:
                """Roles for one member, or None when Discord would not say."""
                async with semaphore:
                    for _attempt in range(4):
                        response = await runtime.http.get(
                            f"{DISCORD_API}/guilds/{guild_id}/members/{member_id}",
                            headers=headers,
                        )
                        if response.status_code == 429:
                            retry = float(response.headers.get("Retry-After", "1") or 1)
                            await asyncio.sleep(min(10.0, retry))
                            continue
                        break
                if response.status_code == 404:
                    return set()  # not in the server, so it holds no protected role
                if response.status_code >= 400:
                    return None
                return {str(r) for r in response.json().get("roles", [])}

            missing = [m for m in members if m["discord_user_id"] not in held_by]
            if missing:
                extra = await asyncio.gather(
                    *(current_roles(m["discord_user_id"]) for m in missing)
                )
                for member, roles in zip(missing, extra, strict=True):
                    if roles is not None:
                        held_by[member["discord_user_id"]] = roles

            kept: list[dict[str, Any]] = []
            for member in members:
                held = held_by.get(member["discord_user_id"])
                if held is None:
                    unverified.append(
                        {
                            "discord_user_id": member["discord_user_id"],
                            "discord_username": member["discord_username"],
                            "reason": "Discord would not say which roles they hold",
                        }
                    )
                    continue
                hit = held & excluded_ids
                if hit:
                    skipped.append(
                        {
                            "discord_user_id": member["discord_user_id"],
                            "discord_username": member["discord_username"],
                            "roles": ", ".join(sorted(role_names.get(r, r) for r in hit)),
                        }
                    )
                else:
                    kept.append(member)
            members = kept

        if payload.dry_run:
            return {
                "matched": len(members),
                "members": members[:500],
                "skipped": skipped,
                "unverified": unverified,
            }
        changed: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        async def apply(member: dict[str, Any]) -> None:
            url = (
                f"{DISCORD_API}/guilds/{guild_id}/members/{member['discord_user_id']}"
                f"/roles/{payload.role_id}"
            )
            async with semaphore:
                for _attempt in range(4):
                    response = await runtime.http.request(
                        "PUT" if payload.action == "add" else "DELETE",
                        url,
                        headers={**headers, "X-Audit-Log-Reason": "Major's Lair engagement bot"},
                    )
                    if response.status_code == 429:
                        retry = float(response.headers.get("Retry-After", "1") or 1)
                        await asyncio.sleep(min(10.0, retry))
                        continue
                    break
            entry = {
                "discord_user_id": member["discord_user_id"],
                "discord_username": member["discord_username"],
            }
            if response.status_code in {204, 200}:
                changed.append(entry)
            else:
                detail = ""
                try:
                    detail = str(response.json().get("message", ""))
                except Exception:  # noqa: BLE001
                    detail = response.text[:120]
                if response.status_code == 403:
                    detail = (
                        "Missing permission: give the bot the Manage Roles permission and "
                        "move its role above the target role"
                    )
                elif response.status_code == 404:
                    detail = "Not in the server anymore"
                failed.append({**entry, "error": f"{response.status_code}: {detail}"})

        await asyncio.gather(*(apply(member) for member in members))
        await runtime.repository.append_audit(
            event_type="admin_bulk_role",
            actor_discord_id=str(admin["discord_user_id"]),
            details={
                "role_id": payload.role_id,
                "action": payload.action,
                "filters": payload.filters.model_dump(),
                "matched": len(members),
                "changed": len(changed),
                "failed": len(failed),
                "skipped_by_role": len(skipped),
                "unverified": len(unverified),
                "exclude_role_ids": sorted(excluded_ids),
            },
        )
        return {
            "matched": len(members),
            "changed": changed,
            "failed": failed,
            "skipped": skipped,
            "unverified": unverified,
        }

    @app.get("/api/users/export")
    async def export_users(
        _: Admin,
        search: str = "",
        active: bool | None = None,
        protected: bool | None = None,
        linked: bool | None = None,
        x_ok: bool | None = None,
        points: str = "any",
        sort: str = "score_desc",
        joined: str = "any",
        threshold: float | None = None,
    ) -> Response:
        """The current member list, with the same filters as the page, as a CSV sheet."""
        config_values = await runtime.repository.get_config()
        if points == "low":
            threshold = (
                threshold
                if threshold is not None
                else float(config_values["low_activity_threshold"])
            )
        else:
            threshold = None
        grace = int(config_values.get("newcomer_grace_days", DEFAULT_CONFIG["newcomer_grace_days"]))
        page = await runtime.repository.paginated_users(
            search=search,
            active=active,
            protected=protected,
            linked=linked,
            x_ok=x_ok,
            points=points,
            low_threshold=threshold,
            sort=sort,
            joined=joined,
            grace_days=grace,
            page=1,
            page_size=10000,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(
            [
                "rank",
                "discord_username",
                "discord_id",
                "x_handle",
                "points",
                "protected",
                "special_role_names",
                "x_status",
                "active",
                "last_signal",
                "linked_at",
                "discord_joined_at",
            ]
        )
        for index, item in enumerate(page["items"], start=1):
            writer.writerow(
                [
                    index,
                    item["discord_username"],
                    item["discord_user_id"],
                    item["twitter_handle"],
                    item["score"],
                    "YES" if item["special_role"] else "NO",
                    item["special_role_names"],
                    item["x_status"] or ("ok" if item["twitter_user_id"] else "not linked"),
                    "active" if item["active"] else "inactive",
                    item["last_active_at"],
                    item["linked_at"],
                    item["discord_joined_at"],
                ]
            )
        stamp = time.strftime("%Y-%m-%d")
        return Response(
            content="\ufeff" + buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="majors-lair-members-{stamp}.csv"'
            },
        )

    @app.post("/api/users/check-follows")
    async def check_follows(admin: MutatingAdmin, include_protected: bool = True) -> dict[str, Any]:
        """Check which linked members follow the primary and secondary accounts."""
        return await runtime.service.check_follows(
            actor_discord_id=str(admin["discord_user_id"]),
            include_protected=include_protected,
        )

    @app.get("/api/users/follow-estimate")
    async def follow_estimate(_: Admin) -> dict[str, Any]:
        """Roughly what the follow check will cost, from the two accounts' follower counts."""
        config_values = await runtime.repository.get_config()
        rules = ScoringRules.from_mapping(config_values)
        accounts: list[dict[str, Any]] = []
        total = 0
        for slot, handle in (("primary", rules.primary_handle), ("secondary", rules.secondary_handle)):
            if not handle:
                continue
            try:
                profile = await runtime.service.twitter.get_user_info(handle)
            except Exception as exc:  # noqa: BLE001
                accounts.append({"slot": slot, "handle": handle, "error": str(exc)})
                continue
            followers = int(
                profile.get("followers")
                or profile.get("followers_count")
                or profile.get("followersCount")
                or 0
            )
            total += followers
            accounts.append({"slot": slot, "handle": handle, "followers": followers})
        linked = sum(
            1 for user in await runtime.repository.list_users(active_only=True) if user.twitter_user_id
        )
        # twitterapi.io bills one credit per follower returned, 200 per page.
        return {"accounts": accounts, "linked_members": linked, "credits": total}

    @app.post("/api/users/verify-x")
    async def verify_x_accounts(
        admin: MutatingAdmin, payload: VerifyRequest | None = None
    ) -> dict[str, Any]:
        options = payload or VerifyRequest()
        outcome = await runtime.service.verify_linked_accounts(
            actor_discord_id=str(admin["discord_user_id"]),
            include_protected=not options.skip_protected,
        )
        if outcome.get("error"):
            raise HTTPException(status_code=502, detail=str(outcome["error"]))
        return outcome

    @app.post("/api/users/link")
    async def link_user(payload: LinkUserRequest, admin: MutatingAdmin) -> dict[str, str]:
        old_handle, handle, twitter_id = await runtime.service.link_user(
            discord_user_id=payload.discord_user_id,
            discord_username=payload.discord_username,
            handle=payload.twitter_handle,
        )
        await runtime.repository.append_audit(
            event_type="admin_member_linked",
            actor_discord_id=str(admin["discord_user_id"]),
            subject_discord_id=payload.discord_user_id,
            old_value=old_handle,
            new_value=handle,
        )
        return {"twitter_handle": handle, "twitter_user_id": twitter_id}

    @app.post("/api/users/import")
    async def import_users(payload: ImportRequest, admin: MutatingAdmin) -> dict[str, Any]:
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for row in payload.rows:
            if row.discord_user_id in seen:
                raise HTTPException(
                    status_code=422,
                    detail=f"Discord ID {row.discord_user_id} appears more than once",
                )
            seen.add(row.discord_user_id)
            rows.append(
                {
                    "discord_user_id": row.discord_user_id,
                    "discord_username": row.discord_username.strip(),
                    "twitter_handle": row.twitter_handle,
                    "special_role": row.special_role,
                    "special_role_names": row.special_role_names,
                }
            )
        results = await runtime.service.import_links(
            rows, actor_discord_id=str(admin["discord_user_id"])
        )
        summary: dict[str, int] = {}
        for item in results:
            summary[item["status"]] = summary.get(item["status"], 0) + 1
        return {"summary": summary, "results": results}

    @app.patch("/api/users/{discord_user_id}")
    async def update_user(
        discord_user_id: str, payload: UserUpdate, admin: MutatingAdmin
    ) -> dict[str, Any]:
        if (
            payload.active is None
            and payload.special_role is None
            and payload.special_role_names is None
            and payload.discord_username is None
        ):
            raise HTTPException(status_code=422, detail="Nothing to update")
        actor_id = str(admin["discord_user_id"])
        user = None
        if payload.discord_username is not None:
            before = await runtime.repository.get_user(discord_user_id)
            if before is None:
                raise HTTPException(status_code=404, detail="Member not found")
            user, _ = await runtime.repository.register_member(
                discord_user_id=discord_user_id, discord_username=payload.discord_username.strip()
            )
            if before.discord_username != user.discord_username:
                await runtime.repository.append_audit(
                    event_type="admin_member_renamed",
                    actor_discord_id=actor_id,
                    subject_discord_id=discord_user_id,
                    old_value=before.discord_username,
                    new_value=user.discord_username,
                )
        if payload.active is not None:
            try:
                user = await runtime.repository.set_user_active(discord_user_id, payload.active)
            except LinkConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if user is None:
                raise HTTPException(status_code=404, detail="Member not found")
            await runtime.repository.append_audit(
                event_type="admin_member_toggled",
                actor_discord_id=actor_id,
                subject_discord_id=discord_user_id,
                new_value="active" if payload.active else "inactive",
            )
        if payload.special_role is not None or payload.special_role_names is not None:
            before = await runtime.repository.get_user(discord_user_id)
            if before is None:
                raise HTTPException(status_code=404, detail="Member not found")
            protected = (
                payload.special_role if payload.special_role is not None else before.special_role
            )
            user = await runtime.repository.set_special_role(
                discord_user_id,
                special_role=protected,
                special_role_names=payload.special_role_names,
            )
            assert user is not None
            await runtime.repository.append_audit(
                event_type="admin_member_protection_changed",
                actor_discord_id=actor_id,
                subject_discord_id=discord_user_id,
                old_value="protected" if before.special_role else "regular",
                new_value="protected" if user.special_role else "regular",
                details={"special_role_names": user.special_role_names},
            )
        assert user is not None
        return asdict(user)

    @app.delete("/api/users/{discord_user_id}")
    async def delete_user(
        discord_user_id: str, admin: MutatingAdmin, ignore_in_sync: bool = True
    ) -> dict[str, Any]:
        """Erase a member, their scored actions and their point adjustments.

        Deactivating keeps somebody in the registry with their history; this is for records
        that should not exist at all, such as an admin's own account or an alt. Frozen
        leaderboard snapshots and the audit trail keep their copy, so closed cycles still
        add up. Nothing happens to the person's Discord account.
        """
        removed = await runtime.repository.delete_member(discord_user_id)
        if removed is None:
            raise HTTPException(status_code=404, detail="No member with that Discord ID")
        ignored = False
        if ignore_in_sync:
            # They are probably still in the server, so without this the next sync would put
            # the record straight back.
            config_values = await runtime.repository.get_config()
            current = [
                value.strip()
                for value in config_values.get("sync_ignored_discord_ids", "").split(",")
                if value.strip()
            ]
            if discord_user_id not in current:
                current.append(discord_user_id)
                await runtime.repository.set_config_values(
                    {"sync_ignored_discord_ids": ",".join(current)},
                    actor_discord_id=str(admin["discord_user_id"]),
                )
            ignored = True
        await runtime.repository.append_audit(
            event_type="admin_member_deleted",
            actor_discord_id=str(admin["discord_user_id"]),
            subject_discord_id=discord_user_id,
            old_value=removed["discord_username"],
            new_value="",
            details={**removed, "ignored_in_sync": ignored},
        )
        return {**removed, "ignored_in_sync": ignored}

    @app.get("/api/users/{discord_user_id}")
    async def member_detail(discord_user_id: str, _: Admin) -> dict[str, Any]:
        """One member and the arithmetic behind their score, for the member page."""
        if not discord_user_id.isdigit():
            raise HTTPException(status_code=404, detail="No member with that Discord ID")
        detail = await runtime.repository.member_breakdown(discord_user_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="No member with that Discord ID")
        return detail

    @app.get("/api/users/{discord_user_id}/adjustments")
    async def member_adjustments(discord_user_id: str, _: Admin) -> list[dict[str, Any]]:
        return await runtime.repository.list_adjustments(discord_user_id)

    @app.post("/api/users/{discord_user_id}/adjust")
    async def adjust_member_points(
        discord_user_id: str, payload: AdjustRequest, admin: MutatingAdmin
    ) -> dict[str, Any]:
        """Add or remove points, or transfer them to another member (id, Discord or X handle)."""
        config_values = await runtime.repository.get_config()
        cycle_id = config_values.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        target_id: str | None = None
        if payload.transfer_to:
            target = await runtime.repository.find_member(payload.transfer_to)
            if target is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No member matches '{payload.transfer_to}' (use Discord ID, "
                    "Discord handle or X handle)",
                )
            target_id = target.discord_user_id
        try:
            rows = await runtime.repository.adjust_points(
                cycle_id=cycle_id,
                discord_user_id=discord_user_id,
                points=payload.points,
                reason=payload.reason.strip(),
                actor_discord_id=str(admin["discord_user_id"]),
                transfer_to=target_id,
            )
        except DatabaseRepositoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await runtime.repository.append_audit(
            event_type="admin_points_transferred" if target_id else "admin_points_adjusted",
            actor_discord_id=str(admin["discord_user_id"]),
            subject_discord_id=discord_user_id,
            new_value=f"{payload.points:+g}",
            details={
                "reason": payload.reason.strip(),
                "transfer_to": target_id or "",
                "adjustments": rows,
            },
        )
        member = await runtime.repository.get_user(discord_user_id)
        return {"adjustments": rows, "member": asdict(member) if member else None}

    @app.post("/api/users/{discord_user_id}/scan")
    async def scan_member(
        discord_user_id: str, payload: MemberScanRequest, admin: MutatingAdmin
    ) -> dict[str, Any]:
        """Deep-check a single member's own timeline for the period and score it."""
        try:
            return await runtime.service.scan_member(
                discord_user_id=discord_user_id,
                period=payload.period,
                actor_discord_id=str(admin["discord_user_id"]),
                max_pages=payload.max_pages,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TwitterApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/users/sync-discord")
    async def sync_discord_members(admin: MutatingAdmin) -> dict[str, Any]:
        """Register every human member of the Discord server who is not in the registry yet.

        Uses the bot token against the REST API, so it works even when the gateway bot is
        disabled. Discord only serves the member list when the Server Members Intent is
        enabled for the application.
        """
        headers = {"Authorization": f"Bot {runtime.settings.discord_token}"}
        guild_id = runtime.settings.discord_guild_id
        members = await _fetch_guild_members()

        # Discord roles that mean "protected": picked roles (protected_role_ids) and, for
        # older settings, typed names (protected_role_names).
        config_values = await runtime.repository.get_config()
        wanted_ids = split_setting(config_values.get("protected_role_ids", ""))
        wanted_names = split_setting(config_values.get("protected_role_names", ""))
        ignored_ids = {
            value.strip()
            for value in config_values.get("sync_ignored_discord_ids", "").split(",")
            if value.strip()
        }
        role_names: dict[str, str] = {}
        unmatched_role_names: list[dict[str, Any]] = []
        missing_role_ids: list[str] = []
        # If protection is configured but the server's roles cannot be read, the matched set
        # would come back empty and the pass below would unprotect everyone. Skip it instead.
        roles_unreadable = False
        if wanted_ids or wanted_names:
            roles_response = await runtime.http.get(
                f"{DISCORD_API}/guilds/{guild_id}/roles", headers=headers
            )
            if roles_response.status_code < 400 and isinstance(roles_response.json(), list):
                matched = match_protected_roles(
                    [r for r in roles_response.json() if isinstance(r, dict)],
                    wanted_ids,
                    wanted_names,
                )
                role_names = matched.roles
                unmatched_role_names = matched.unmatched_names
                missing_role_ids = matched.missing_ids
            else:
                roles_unreadable = True

        registry = {user.discord_user_id: user for user in await runtime.repository.list_users()}
        added: list[dict[str, str]] = []
        renamed: list[dict[str, str]] = []
        bots = 0
        ignored_present = 0
        role_protection: dict[str, str] = {}
        for member in members:
            user = member.get("user") or {}
            user_id = str(user.get("id") or "")
            if not user_id:
                continue
            if user.get("bot") or user.get("system"):
                bots += 1
                continue
            if user_id in ignored_ids:
                ignored_present += 1
                continue
            # Store the Discord handle (username), not the nickname, so the registry matches
            # what admins see in profiles and what /link-twitter records.
            handle = str(user.get("username") or user.get("global_name") or user_id)[:120]
            matched_roles = sorted(
                {role_names[str(r)] for r in member.get("roles", []) if str(r) in role_names}
            )
            existing = registry.get(user_id)
            role_protection[user_id] = ", ".join(matched_roles)
            joined_at = None
            if member.get("joined_at"):
                try:
                    joined_at = parse_datetime(member["joined_at"])
                except (ValueError, TypeError):
                    joined_at = None
            if existing is not None:
                await runtime.repository.register_member(
                    discord_user_id=user_id,
                    discord_username=handle,
                    discord_joined_at=joined_at,
                )
                if existing.discord_username != handle:
                    renamed.append(
                        {
                            "discord_user_id": user_id,
                            "old": existing.discord_username,
                            "discord_username": handle,
                        }
                    )
                continue
            await runtime.repository.register_member(
                discord_user_id=user_id,
                discord_username=handle,
                discord_joined_at=joined_at,
            )
            added.append(
                {
                    "discord_user_id": user_id,
                    "discord_username": handle,
                    "roles": ", ".join(matched_roles),
                }
            )

        protection = (
            {"gained": [], "lost": []}
            if roles_unreadable
            else await runtime.repository.apply_role_protection(role_protection)
        )
        protected_by_role = protection["gained"]
        unprotected_by_role = protection["lost"]

        discord_ids = {
            str((m.get("user") or {}).get("id") or "")
            for m in members
            if not (m.get("user") or {}).get("bot")
        }
        left = [
            {"discord_user_id": user.discord_user_id, "discord_username": user.discord_username}
            for user in registry.values()
            if user.active and user.discord_user_id not in discord_ids
        ]
        # Mark them inactive here. Nobody is removed from Discord by this: it drops them from
        # the leaderboard, the reports and future scans, and their history and points are kept
        # so reactivating restores everything.
        #
        # Guard against a truncated member list. If Discord served far fewer members than the
        # registry holds (a rate limit, a permissions change, a half-finished page walk), a
        # blind deactivation would empty the community, so refuse and say why instead.
        registry_active_before = sum(1 for user in registry.values() if user.active)
        visible_humans = len(members) - bots
        partial_list = bool(left) and visible_humans < registry_active_before * 0.6
        deactivated: list[dict[str, str]] = []
        if left and not partial_list:
            deactivated = await runtime.repository.deactivate_members(
                {item["discord_user_id"] for item in left}
            )
        present = [user for user in registry.values() if user.discord_user_id in discord_ids]
        # Present in Discord but inactive here. Not reactivated automatically: an admin may
        # have deactivated them on purpose, and sync should not quietly undo that.
        back_in_server = [
            {"discord_user_id": user.discord_user_id, "discord_username": user.discord_username}
            for user in present
            if not user.active
        ]
        present_active = sum(1 for user in present if user.active)
        present_inactive = len(present) - present_active
        final_registry = await runtime.repository.list_users()
        registry_active = sum(1 for user in final_registry if user.active)
        await runtime.repository.append_audit(
            event_type="admin_members_synced",
            actor_discord_id=str(admin["discord_user_id"]),
            details={
                "discord_members": len(members),
                "bots_skipped": bots,
                "added": len(added),
                "renamed": len(renamed),
                "protected_by_role": len(protected_by_role),
                "unprotected_by_role": len(unprotected_by_role),
                "left_server": len(left),
                "deactivated": len(deactivated),
                "ignored": ignored_present,
                "partial_list_guard": partial_list,
            },
        )
        return {
            "discord_members": len(members) - bots,
            "bots_skipped": bots,
            "already_registered": len(members) - bots - len(added),
            "already_registered_active": present_active,
            "already_registered_inactive": present_inactive,
            "registry_active": registry_active,
            "registry_inactive": len(final_registry) - registry_active,
            "added": added,
            "renamed": renamed,
            "protected_by_role": protected_by_role,
            "unprotected_by_role": unprotected_by_role,
            "protected_roles_configured": sorted(role_names.values()),
            "unmatched_role_names": unmatched_role_names,
            "missing_role_ids": missing_role_ids,
            "roles_unreadable": roles_unreadable,
            "left_server": left,
            "deactivated": deactivated,
            "back_in_server": back_in_server,
            "ignored": ignored_present,
            "partial_list_guard": partial_list,
        }

    @app.get("/api/actions")
    async def actions(
        _: Admin,
        action_type: str = "",
        active: bool | None = None,
        search: str = "",
        discord_user_id: str = "",
        sort: str = "occurred_desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_actions(
            action_type=action_type,
            active=active,
            search=search,
            discord_user_id=discord_user_id,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/diagnose")
    async def diagnose(payload: DiagnoseRequest, admin: MutatingAdmin) -> dict[str, Any]:
        """Why is this tweet (not) counted? Checks linking, the log, and every fetch path."""
        try:
            outcome = await runtime.service.diagnose_tweet(payload.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TwitterApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await runtime.repository.append_audit(
            event_type="tweet_diagnosed",
            actor_discord_id=str(admin["discord_user_id"]),
            details={"tweet_id": outcome.get("tweet_id", ""), "findings": outcome["findings"]},
        )
        return outcome

    @app.get("/api/audit")
    async def audit(
        _: Admin, event_type: str = "", search: str = "", page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_audit(
            event_type=event_type, search=search, page=page, page_size=page_size
        )

    @app.get("/api/tracked-posts")
    async def tracked_posts(_: Admin, active_only: bool = False) -> list[dict[str, Any]]:
        return await runtime.repository.list_tracked_posts(active_only=active_only)

    @app.post("/api/tracked-posts")
    async def track_post(payload: TrackPostRequest, admin: MutatingAdmin) -> dict[str, Any]:
        try:
            tweet = await runtime.service.track_post(
                url=payload.url, actor_discord_id=str(admin["discord_user_id"])
            )
        except TwitterApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return asdict(tweet)

    @app.patch("/api/tracked-posts/{tweet_id}")
    async def set_tracked_post(
        tweet_id: str, payload: ActiveToggle, admin: MutatingAdmin
    ) -> dict[str, bool]:
        rows = await runtime.repository.list_tracked_posts(active_only=False)
        row = next((item for item in rows if item["tweet_id"] == tweet_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Tracked post not found")
        await runtime.repository.upsert_tracked_posts([{**row, "active": payload.active}])
        await runtime.repository.append_audit(
            event_type="tracked_post_toggled",
            actor_discord_id=str(admin["discord_user_id"]),
            subject_discord_id="",
            old_value=str(row["active"]),
            new_value=str(payload.active),
            details={"tweet_id": tweet_id},
        )
        return {"active": payload.active}

    @app.get("/api/config")
    async def config(_: Admin) -> list[dict[str, Any]]:
        return await runtime.repository.list_config_entries()

    @app.put("/api/config")
    async def update_config(payload: ConfigUpdate, admin: MutatingAdmin) -> dict[str, int]:
        editable = set(DEFAULT_CONFIG).difference({"current_cycle_id", "cycle_started_at"})
        unknown = set(payload.values).difference(editable)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported settings: {', '.join(unknown)}",
            )
        current = await runtime.repository.get_config()
        candidate = {**current, **payload.values}
        try:
            ScoringRules.from_mapping(candidate)
            parse_period(candidate["default_check_period"])
            parse_period(candidate["default_refresh_period"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # The dashboard submits the whole form, so most keys arrive unchanged. Auditing all
        # of them produced entries claiming 33 settings changed when one did.
        changed = {
            key: value
            for key, value in payload.values.items()
            if str(current.get(key, "")) != str(value)
        }
        if not changed:
            return {"rescored_actions": 0, "changed": 0}
        await runtime.repository.set_config_values(
            changed, actor_discord_id=str(admin["discord_user_id"])
        )
        count = await runtime.service.rescore_current_cycle(config=candidate)
        await runtime.repository.append_audit(
            event_type="config_updated",
            actor_discord_id=str(admin["discord_user_id"]),
            details={
                "changes": {
                    key: {"from": current.get(key, ""), "to": value}
                    for key, value in sorted(changed.items())
                },
                "rescored_actions": count,
            },
        )
        return {"rescored_actions": count, "changed": len(changed)}

    async def run_scan(
        scan_id: str,
        period: str,
        actor_id: str,
        *,
        verify_x: bool = True,
        include_protected: bool | None = None,
        read_timelines: bool | None = None,
        timeline_pages: int | None = None,
    ) -> None:
        try:
            await runtime.service.scan(
                period=period,
                actor_discord_id=actor_id,
                source="admin",
                scan_id=scan_id,
                verify_x=verify_x,
                include_protected=include_protected,
                read_timelines=read_timelines,
                timeline_pages=timeline_pages,
            )
        except Exception:
            LOGGER.exception("Admin-triggered scan %s failed", scan_id)

    @app.post("/api/scans")
    async def start_scan(payload: ScanRequest, admin: MutatingAdmin) -> dict[str, str]:
        try:
            parse_period(payload.period)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if runtime.scan_task and not runtime.scan_task.done():
            raise HTTPException(status_code=409, detail="A dashboard scan is already running")
        actor_id = str(admin["discord_user_id"])
        scan_id = await runtime.repository.create_scan_run(
            period=payload.period, triggered_by=actor_id, source="admin"
        )
        runtime.scan_task = asyncio.create_task(
            run_scan(
                scan_id,
                payload.period,
                actor_id,
                verify_x=payload.verify_x,
                include_protected=(
                    None if payload.skip_protected is None else not payload.skip_protected
                ),
                read_timelines=payload.read_timelines,
                timeline_pages=payload.timeline_pages,
            ),
            name=f"scan-{scan_id}",
        )
        return {"scan_id": scan_id, "status": "running"}

    @app.get("/api/scans/estimate")
    async def scan_estimate(_: Admin, period: str = "24h") -> dict[str, Any]:
        """What a scan of ``period`` will touch, and what the last similar scan cost."""
        try:
            _, period_label = parse_period(period)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        users = await runtime.repository.list_users(active_only=True)
        linked = [user for user in users if user.twitter_user_id]
        config_values = await runtime.repository.get_config()
        skip_default = parse_bool(
            config_values.get("skip_protected_members", DEFAULT_CONFIG["skip_protected_members"])
        )
        try:
            estimate = await runtime.service.estimate_scan(period)
        except TwitterApiError as exc:
            estimate = {"error": str(exc)}
        previous = next(
            (
                scan
                for scan in await runtime.repository.recent_scans(50)
                if scan.get("status") == "complete" and scan.get("period") == period_label
            ),
            None,
        )
        previous_credits: int | None = None
        if previous:
            summary = previous.get("summary") or {}
            items = int(summary.get("tweets_returned") or 0)
            requests = int(summary.get("api_requests") or 0)
            checked = int(summary.get("x_checked") or 0)
            # Tweets cost 15 credits each with a 15-credit floor per request; profile checks
            # cost about 10 credits each in batches.
            previous_credits = max(items, requests) * 15 + checked * 10
        return {
            "period": period_label,
            "linked_members": len(linked),
            "protected_linked": sum(1 for user in linked if user.special_role),
            "unlinked_members": len(users) - len(linked),
            "tracked_posts": len(await runtime.repository.list_tracked_posts(active_only=True)),
            "verification_credits_per_account": 10,
            "skip_protected_default": skip_default,
            "estimate": estimate,
            "previous_scan": (
                {
                    "completed_at": previous.get("completed_at", ""),
                    "discovered": (previous.get("summary") or {}).get("discovered", 0),
                    "api_requests": (previous.get("summary") or {}).get("api_requests", 0),
                    "items_returned": (previous.get("summary") or {}).get("tweets_returned", 0),
                    "credits": previous_credits,
                }
                if previous
                else None
            ),
        }

    @app.get("/api/scans")
    async def scans(_: Admin, limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return await runtime.repository.recent_scans(limit)

    @app.get("/api/snapshots")
    async def snapshots(_: Admin) -> list[dict[str, Any]]:
        return await runtime.repository.list_snapshots()

    @app.get("/api/scan-runs")
    async def scan_runs(_: Admin, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_scans(page=page, page_size=page_size)

    @app.get("/api/low-activity")
    async def low_activity(
        _: Admin, threshold: float | None = None, include_protected: bool = False
    ) -> dict[str, Any]:
        config_values = await runtime.repository.get_config()
        if threshold is None:
            threshold = float(config_values["low_activity_threshold"])
        grace = int(config_values.get("newcomer_grace_days", DEFAULT_CONFIG["newcomer_grace_days"]))
        if include_protected:
            users_list = await runtime.repository.low_activity(
                threshold, include_protected=True, grace_days=0
            )
            return {
                "threshold": threshold,
                "include_protected": True,
                "newcomer_grace_days": 0,
                "excluded_protected": 0,
                "excluded_newcomers": 0,
                "items": [asdict(user) for user in users_list],
            }
        report = await runtime.repository.low_activity_report(threshold, grace_days=grace)
        return {**report, "include_protected": False}

    @app.post("/api/reset")
    async def reset(payload: ResetRequest, admin: MutatingAdmin) -> dict[str, Any]:
        if payload.confirmation != "RESET LEADERBOARD":
            raise HTTPException(status_code=422, detail="Type RESET LEADERBOARD exactly")
        actor_id = str(admin["discord_user_id"])
        old_cycle, new_cycle, snapshots = await runtime.repository.reset_leaderboard(actor_id)
        await runtime.repository.append_audit(
            event_type="leaderboard_reset",
            actor_discord_id=actor_id,
            old_value=old_cycle,
            new_value=new_cycle,
            details={"snapshots": snapshots, "source": "admin"},
        )
        return {
            "old_cycle": old_cycle,
            "new_cycle": new_cycle,
            "snapshots": snapshots,
        }

    static_dir = Path("static").resolve()
    if static_dir.exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app
