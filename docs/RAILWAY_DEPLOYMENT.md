# Railway deployment

## Recommended topology

Use one Railway project containing:

1. **`majors-lair-app`** — the repository's Docker service. It serves the React admin site and
   FastAPI API and maintains the Discord gateway connection.
2. **`Postgres`** — Railway's managed PostgreSQL service, connected only over Railway's private
   network.

This is simpler and less expensive than splitting the site, API, and bot into separate services.
It also keeps dashboard actions and Discord commands on the same scoring engine.

## 1. Prepare Discord

In the Discord Developer Portal for the bot application:

1. Open **OAuth2 → General**.
2. After Railway generates the app domain, add
   `https://YOUR-DOMAIN.up.railway.app/auth/callback` as a redirect.
3. Keep the existing bot installed with `bot` and `applications.commands` scopes.
4. Give the bot permission to view the guild and read member/role information. The configured
   audit channel also needs View Channel, Send Messages, and Embed Links.

The web login requests `identify`, `guilds`, and `guilds.members.read`. The service re-checks the
member's current guild roles using the bot on every protected API request.

## 2. Create Railway services

1. Create a Railway project from the GitHub repository.
2. Add a PostgreSQL database to that project.
3. Generate a public domain for the application service.
4. Keep the PostgreSQL public network disabled unless temporary external database access is truly
   needed.

## 3. Set application variables

Set these on `majors-lair-app`:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | Discord bot token |
| `DISCORD_GUILD_ID` | Major's Lair server ID |
| `DISCORD_CLIENT_ID` | Discord application ID |
| `DISCORD_CLIENT_SECRET` | Discord OAuth client secret |
| `DISCORD_OAUTH_REDIRECT_URI` | `https://YOUR-DOMAIN/auth/callback` |
| `DISCORD_AUDIT_CHANNEL_ID` | Private engagement-audit channel ID (optional) |
| `ADMIN_ROLE_IDS` | Comma-separated dashboard/admin role IDs |
| `TWITTERAPI_IO_KEY` | twitterapi.io key |
| `DATABASE_URL` | Railway reference: `${{Postgres.DATABASE_URL}}` |
| `APP_BASE_URL` | `https://YOUR-DOMAIN` |
| `TRUSTED_HOSTS` | `YOUR-DOMAIN` without `https://` |
| `SESSION_COOKIE_SECURE` | `true` |
| `ADMIN_SESSION_TTL_HOURS` | `12` |
| `RUN_DISCORD_BOT` | `true` |
| `LOG_LEVEL` | `INFO` |

Railway injects `PORT`; do not hardcode it in production.

## 4. Deploy and verify

The Docker entrypoint applies pending Alembic migrations before the service starts.

1. Confirm `/healthz` returns `{"status":"ok"}`.
2. Open the public domain and sign in through Discord.
3. Confirm the Overview page reports the Discord bot online.
4. Link one test member and run a `24h` scan.
5. Confirm the activity decision and score match `/my-history` in Discord.
6. Change one scoring value, save, and confirm the audit record and rescore count.
7. Test an account without an admin role; it must receive access denied.

## Secrets requested from Major

Ask Major to share secrets in a password manager or Railway's variables—not Discord chat, email,
or GitHub issues. The deployer needs:

- Discord bot token
- Discord application/client ID and client secret
- server ID, optional audit-channel ID, and approved admin role IDs
- twitterapi.io API key
- confirmation of the two X handles
- either access to the Discord Developer Portal or help adding the production OAuth callback

No Google account, service-account key, Sheet ID, X password, X cookies, or X OAuth token is needed.
