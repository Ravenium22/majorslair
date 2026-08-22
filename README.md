# Major's Lair X Engagement Tracker

A production-oriented Discord bot that tracks public X engagement for `@m_m3l` and
`@majorslair`, scores linked members, and stores the complete operational record in Google
Sheets. X data comes from **twitterapi.io** read endpoints; the bot does not need X OAuth,
login cookies, an X password, or a proxy.

## What is included

- One-to-one Discord ↔ X linking with canonical X user-ID duplicate protection
- Handle-change history and link/unlink audit events
- On-demand replies, quote tweets, retweets, and organic mention scans
- Editable scoring weights, blacklist, bonuses, cost/page caps, and activity threshold
- Idempotent action keys, repeated-text suppression, and a configurable per-day cap
- Public leaderboard, personal score, and transparent personal scoring history
- Low-activity report for admin review
- Confirmed leaderboard reset with immutable historical snapshots and a new-cycle timestamp
- Deleted-content reconciliation when an endpoint completed all pages
- Sheet and private Discord audit logs
- Retry/backoff for rate limits and transient twitterapi.io failures
- Docker and Railway deployment files

## Commands

Member commands:

- `/link-twitter @handle`
- `/unlink-twitter`
- `/leaderboard`
- `/my-score`
- `/my-history`

Admin commands (Discord administrators or roles in `ADMIN_ROLE_IDS`):

- `/check-engagement [period]` — accepts `24h`, `7d`, `30d`, up to 31 days
- `/refresh-engagement` — uses `default_refresh_period` from `Config`
- `/track-post <url>`
- `/low-activity-report [threshold]`
- `/reset-leaderboard` — requires a button confirmation
- `/sync-sheet`

## Setup

### 1. Discord application

1. Create an application at the Discord Developer Portal and add a bot.
2. Copy its token into `DISCORD_TOKEN`.
3. Install it in the server with the `bot` and `applications.commands` scopes.
4. Give it View Channels, Send Messages, Embed Links, and Read Message History in the command
   and audit channels.
5. Set `DISCORD_GUILD_ID` during setup so slash-command changes appear immediately. If omitted,
   commands are global and Discord can take longer to publish them.
6. Create a private `#audit-log-engagement` channel and put its ID in
   `DISCORD_AUDIT_CHANNEL_ID`.

No privileged Discord gateway intents are required.

### 2. twitterapi.io

1. Create an account at the [twitterapi.io dashboard](https://twitterapi.io/dashboard).
2. Put the API key in `TWITTERAPI_IO_KEY`.

The integration uses these exact, read-only endpoints:

- `GET /twitter/user/info?userName=`
- `GET /twitter/user/last_tweets?userName=&includeReplies=false`
- `GET /twitter/tweet/replies?tweetId=&sinceTime=&untilTime=&queryType=Latest`
- `GET /twitter/tweet/quotes?tweetId=`
- `GET /twitter/tweet/retweeters?tweetId=`
- `GET /twitter/user/mentions?userName=&sinceTime=&untilTime=&queryType=Latest`
- `GET /twitter/tweets?tweet_ids=`

The bot never stores or asks for `login_cookies`, an X password, or a proxy.

### 3. Google Sheet

1. In Google Cloud, create a project and enable the Google Sheets and Google Drive APIs.
2. Create a service account and download its JSON key.
3. Share the existing community Google Sheet with the service account's `client_email` as an
   editor.
4. Put the Sheet ID—the value between `/d/` and `/edit` in its URL—in `GOOGLE_SHEET_ID`.
5. Use either:
   - `GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json`, or
   - `GOOGLE_SERVICE_ACCOUNT_JSON={...}` for a hosting platform secret.

On startup, the bot extends the existing `Users` header without deleting unrelated columns. It
creates or completes these tabs:

- `ActionsLog`
- `AuditLog`
- `TrackedPosts`
- `Config`
- `HistoricalSnapshots`

Do not rename the bot-owned header fields. Extra columns in `Users` are preserved.

### 4. Run locally

PowerShell:

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
majors-lair-bot
```

Linux/macOS:

```bash
cp .env.example .env
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
majors-lair-bot
```

Fill `.env` before the final command. Never commit `.env` or the service-account JSON.

## First-run checklist

1. Confirm the bot logs `Connected as ...` and all slash commands appear.
2. Check that the six Sheet tabs exist and `Config` contains defaults.
3. Run `/link-twitter` with a test member.
4. Confirm the Sheet `AuditLog` row and private Discord audit message.
5. Run `/check-engagement 24h`.
6. Compare `/my-history`, `ActionsLog`, and `/leaderboard`.
7. Review and announce the scoring rules in [`docs/SCORING.md`](docs/SCORING.md).

## Scan and cost behavior

Scans are strictly admin-triggered. Source posts are discovered for both target accounts, then each
post's replies, quotes, and retweeters are read. Target account mentions are fetched once per target
and matched locally against the linked-user index; the bot does not run one paid search per member.

`Config` page caps put a hard bound on high-engagement posts. A capped or failed endpoint is marked
incomplete, and the bot will **not** deactivate previously logged actions from that scope. This avoids
removing points merely because pagination stopped early. The scan result reports API requests, items
returned, capped scopes, warnings, and a conservative cost estimate.

To stay below the expected monthly budget:

- Keep routine refreshes at `24h`.
- Use `7d` for weekly review and `30d` only for monthly review.
- Increase page caps only when a scan reports an incomplete scope.
- Monitor balance in the twitterapi.io dashboard.

## Reconciliation and resets

Actions are keyed by cycle, type, source, X actor, and action tweet. Re-running the same period cannot
double count them. When a full endpoint scan no longer returns a previously seen action, it becomes
inactive and current-cycle scores are recomputed. If a tracked source post disappears, its associated
reply/quote/retweet actions are deactivated after a successful batch lookup confirms the absence.

`/reset-leaderboard` writes one ranked `HistoricalSnapshots` row per active linked member, zeroes
scores, and sets a new `cycle_started_at`. Later scans cannot award engagement from before that reset.
No action or audit rows are deleted.

## Deployment

### Docker/VPS

```bash
docker build -t majors-lair-bot .
docker run --restart unless-stopped --env-file .env majors-lair-bot
```

### Railway

Push this directory to a private repository, create a Railway service, and add every required `.env`
value as a Railway variable. Prefer `GOOGLE_SERVICE_ACCOUNT_JSON` so no credential file is shipped.
`railway.toml` and the `Dockerfile` supply the build/start configuration.

## Maintenance

- Edit `Config` values in place, then run `/sync-sheet` to rescore the current cycle.
- Keep `discord.py`, `gspread`, and `aiohttp` within the declared compatible major versions.
- Run `python -m pytest -q` before deployment.
- Run `ruff check src tests` for static checks.
- Back up the Sheet before changing headers or doing manual bulk edits.
- If twitterapi.io returns 401, rotate/check `TWITTERAPI_IO_KEY`; 402 means the balance needs a top-up.

## Trust and privacy notes

The requested v1 linking flow intentionally does not require a verification tweet. That makes linking
simple, but it does not prove that a Discord member owns the X account they select. Stable X user IDs
prevent two Discord accounts from linking the same active X account; admins should resolve ownership
disputes through the audit history.

The bot stores public engagement text and Discord/X identifiers in the configured Sheet. Limit Sheet
and audit-channel access to the team members who need it, and follow your server's privacy policy.
