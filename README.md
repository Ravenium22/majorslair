# Major's Lair Engagement Control

A private Discord bot and web admin console for measuring thoughtful X engagement around
`@m_m3l` and `@majorslair`. It uses **twitterapi.io** for public X data, Discord OAuth for admin
access, and PostgreSQL for durable operational data.

Google Sheets is not used. The dashboard is the source of truth for members, scores, scans,
tracked posts, configuration, and audit history.

**Hosting it yourself with no technical background?** Follow
[`docs/HETZNER_GUIDE.md`](docs/HETZNER_GUIDE.md). Everything below is the developer reference.

## What ships

- Discord ↔ X member linking with stable X account IDs and duplicate prevention
- Replies, quotes, retweets, and organic-mention collection through twitterapi.io
- Transparent quality scoring, repeated-text suppression, and daily action caps
- Responsive admin console with overview, members, activity, posts, scoring, and audit views
- Discord OAuth login with live guild-role checks on every protected request
- CSRF-protected mutations and opaque, hashed, expiring database sessions
- Confirmed leaderboard resets with permanent historical snapshots
- PostgreSQL transactions, indexes, Alembic migrations, and no spreadsheet bottleneck
- Startup preflight (`majors-lair-bot check`) with plain-language errors for a bad token, a
  wrong server ID, an unreachable database, or an empty twitterapi.io balance
- Docker Compose stack (app + PostgreSQL + Caddy HTTPS) with a one-line Hetzner installer,
  nightly backups, and a `majorbot` helper command; Railway files are still included

## Discord commands

Member commands:

- `/link-twitter @handle`
- `/unlink-twitter`
- `/leaderboard`
- `/my-score`
- `/my-history`

Admin commands:

- `/check-engagement [period]`
- `/refresh-engagement`
- `/track-post <url>`
- `/low-activity-report [threshold]`
- `/reset-leaderboard`
- `/sync-database`

Server administrators and roles listed in `ADMIN_ROLE_IDS` can use admin commands and sign into
the control panel.

## Architecture

```text
Discord members ── slash commands ──┐
                                    ├── app container (FastAPI + React + Discord bot)
Admins ── Discord OAuth ── web UI ──┤        │
     (HTTPS via Caddy) ─────────────┘        └── PostgreSQL container (private network)
X public data ── twitterapi.io ──────────────┘
```

On Hetzner all three run from `docker-compose.yml` on one small server. On Railway the app is one
Docker service and PostgreSQL is Railway's managed service; Railway injects `DATABASE_URL`.

## Local development

Requirements: Python 3.11+, Node.js 22+, and PostgreSQL.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Push-Location admin
npm install
npm run build
Pop-Location
alembic upgrade head
majors-lair-bot
```

Open `http://localhost:8000`. For local OAuth, add
`http://localhost:8000/auth/callback` as a Discord redirect URI and use it for
`DISCORD_OAUTH_REDIRECT_URI`.

## Environment

Copy [`.env.example`](.env.example). Required values are `DOMAIN` (or `APP_BASE_URL`), the
Discord bot token, guild ID, OAuth client ID and secret, the twitterapi.io key, and a database
URL. `DISCORD_OAUTH_REDIRECT_URI` and `TRUSTED_HOSTS` are derived from `DOMAIN`/`APP_BASE_URL`
when left empty. Run `majors-lair-bot check` to validate everything without starting the bot.

Never commit `.env`, tokens, API keys, database URLs, or screenshots containing them.

## Database and migrations

The container runs `alembic upgrade head` before starting. The initial migration creates:

- `users`
- `actions_log`
- `tracked_posts`
- `config`
- `audit_log`
- `historical_snapshots`
- `scan_runs`
- `admin_sessions`

Scans reconcile idempotent action keys instead of appending duplicates. A complete scan can mark
disappeared actions inactive; incomplete or failed pagination never removes old credit. Resets
snapshot rankings and start a new cycle without deleting action or audit history.

## Deployment

### Hetzner or any Ubuntu VPS (recommended)

`deploy/install.sh` installs Docker, clones the repository into `/opt/majorbot`, generates the
database password, schedules nightly backups, and installs a `majorbot` helper command
(`edit-config`, `check`, `start`, `stop`, `restart`, `logs`, `update`, `backup`, `restore`).
`docker-compose.yml` runs the app, PostgreSQL 16, and Caddy, which obtains and renews the HTTPS
certificate for `DOMAIN` automatically. The click-by-click walkthrough is in
[`docs/HETZNER_GUIDE.md`](docs/HETZNER_GUIDE.md).

### Railway

Use [the Railway deployment guide](docs/RAILWAY_DEPLOYMENT.md). Set `APP_BASE_URL` to the Railway
domain instead of `DOMAIN`. The Docker build compiles the React app, installs the Python service,
applies migrations, and starts the web server and Discord gateway in one process.

Health check: `GET /healthz`

## Verification

```powershell
python -m pytest -q
ruff check src tests
Push-Location admin
npm run build
Pop-Location
docker build -t majors-lair-engagement-control .
```

Scoring behavior is documented in [`docs/SCORING.md`](docs/SCORING.md).

## Security and privacy

The application stores Discord IDs, public X IDs/handles, public engagement text, scores, and
administrator audit events. Limit dashboard access to trusted server roles. OAuth access tokens are
used only during sign-in and are not stored. Admin session tokens are stored only as SHA-256 hashes.

The v1 member link flow does not prove ownership of the selected X account. Stable X IDs prevent
two active Discord members from linking the same X account; ownership disputes should be handled by
admins using the audit trail.
