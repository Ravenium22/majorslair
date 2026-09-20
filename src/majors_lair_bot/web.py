from __future__ import annotations

import asyncio
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

from .database import DatabaseRepository, LinkConflictError, create_database_engine
from .discord_app import EngagementBot
from .engagement import EngagementService
from .scoring import DEFAULT_CONFIG, ScoringRules
from .settings import Settings
from .twitter_client import TwitterApiClient, TwitterApiError
from .utils import parse_bool, parse_period

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


class ScanRequest(BaseModel):
    period: str = Field(default="24h", min_length=2, max_length=10)
    verify_x: bool = True
    skip_protected: bool | None = None


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

    @app.get("/api/users")
    async def users(
        _: Admin,
        search: str = "",
        active: bool | None = None,
        protected: bool | None = None,
        linked: bool | None = None,
        x_ok: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_users(
            search=search,
            active=active,
            protected=protected,
            linked=linked,
            x_ok=x_ok,
            page=page,
            page_size=page_size,
        )

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
        ):
            raise HTTPException(status_code=422, detail="Nothing to update")
        actor_id = str(admin["discord_user_id"])
        user = None
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

    @app.post("/api/users/sync-discord")
    async def sync_discord_members(admin: MutatingAdmin) -> dict[str, Any]:
        """Register every human member of the Discord server who is not in the registry yet.

        Uses the bot token against the REST API, so it works even when the gateway bot is
        disabled. Discord only serves the member list when the Server Members Intent is
        enabled for the application.
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

        registry = {user.discord_user_id: user for user in await runtime.repository.list_users()}
        added: list[dict[str, str]] = []
        bots = 0
        for member in members:
            user = member.get("user") or {}
            user_id = str(user.get("id") or "")
            if not user_id:
                continue
            if user.get("bot") or user.get("system"):
                bots += 1
                continue
            if user_id in registry:
                continue
            # Store the Discord handle (username), not the nickname, so the registry matches
            # what admins see in profiles and what /link-twitter records.
            handle = user.get("username") or user.get("global_name") or user_id
            await runtime.repository.register_member(
                discord_user_id=user_id, discord_username=str(handle)[:120]
            )
            added.append({"discord_user_id": user_id, "discord_username": str(handle)})

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
        present = [user for user in registry.values() if user.discord_user_id in discord_ids]
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
                "left_server": len(left),
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
            "left_server": left,
        }

    @app.get("/api/actions")
    async def actions(
        _: Admin,
        action_type: str = "",
        active: bool | None = None,
        search: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_actions(
            action_type=action_type,
            active=active,
            search=search,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/audit")
    async def audit(
        _: Admin, event_type: str = "", page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_audit(
            event_type=event_type, page=page, page_size=page_size
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
        candidate = {**(await runtime.repository.get_config()), **payload.values}
        try:
            ScoringRules.from_mapping(candidate)
            parse_period(candidate["default_check_period"])
            parse_period(candidate["default_refresh_period"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await runtime.repository.set_config_values(
            payload.values, actor_discord_id=str(admin["discord_user_id"])
        )
        count = await runtime.service.rescore_current_cycle(config=candidate)
        await runtime.repository.append_audit(
            event_type="config_updated",
            actor_discord_id=str(admin["discord_user_id"]),
            details={"keys": sorted(payload.values), "rescored_actions": count},
        )
        return {"rescored_actions": count}

    async def run_scan(
        scan_id: str,
        period: str,
        actor_id: str,
        *,
        verify_x: bool = True,
        include_protected: bool | None = None,
    ) -> None:
        try:
            await runtime.service.scan(
                period=period,
                actor_discord_id=actor_id,
                source="admin",
                scan_id=scan_id,
                verify_x=verify_x,
                include_protected=include_protected,
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

    @app.get("/api/scan-runs")
    async def scan_runs(_: Admin, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        page, page_size = _page(page, page_size)
        return await runtime.repository.paginated_scans(page=page, page_size=page_size)

    @app.get("/api/low-activity")
    async def low_activity(
        _: Admin, threshold: float | None = None, include_protected: bool = False
    ) -> dict[str, Any]:
        if threshold is None:
            config_values = await runtime.repository.get_config()
            threshold = float(config_values["low_activity_threshold"])
        users_list = await runtime.repository.low_activity(
            threshold, include_protected=include_protected
        )
        return {
            "threshold": threshold,
            "include_protected": include_protected,
            "items": [asdict(user) for user in users_list],
        }

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
