# Major's Lair Engagement Control

A private Discord bot and web admin console for measuring thoughtful X engagement around
`@m_m3l` and `@majorslair`. It uses **twitterapi.io** for public X data, Discord OAuth for admin
access, and Railway PostgreSQL for durable operational data.

Google Sheets is not used. The dashboard is the source of truth for members, scores, scans,
tracked posts, configuration, and audit history.

## What ships

- Discord ↔ X member linking with stable X account IDs and duplicate prevention
- Replies, quotes, retweets, and organic-mention collection through twitterapi.io
- Transparent quality scoring, repeated-text suppression, and daily action caps
- Responsive admin console with overview, members, activity, posts, scoring, and audit views
- Discord OAuth login with live guild-role checks on every protected request
- CSRF-protected mutations and opaque, hashed, expiring database sessions
- Confirmed leaderboard resets with permanent historical snapshots
- PostgreSQL transactions, indexes, Alembic migrations, and no spreadsheet bottleneck
- One Railway Docker service for the bot/API/site plus one managed PostgreSQL service

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
                                    ├── Railway application service
Admins ── Discord OAuth ── web UI ──┤   FastAPI + React + Discord bot
                                    │       │
X public data ── twitterapi.io ─────┘       └── Railway private network
                                                    │
                                             Railway PostgreSQL
```

The application and database stay in one Railway project. Railway injects the private
`DATABASE_URL`; the database does not need a public endpoint.

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

Copy [`.env.example`](.env.example). Required secrets are the Discord bot token, Discord OAuth
client secret, and twitterapi.io API key. Railway creates the database URL.

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

Use [the Railway deployment guide](docs/RAILWAY_DEPLOYMENT.md). The Docker build compiles the
React app, installs the Python service, applies migrations, and starts the web server and Discord
gateway in one process.

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
