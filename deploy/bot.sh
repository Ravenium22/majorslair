#!/usr/bin/env bash
# `majorbot` helper: everyday commands for the Major's Lair engagement bot + admin website.
# Installed to /usr/local/bin/majorbot by deploy/install.sh.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "$INSTALL_DIR"

say() { printf '\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
majorbot - manage the Major's Lair engagement bot and admin website

  majorbot edit-config   Open the settings file (.env) in a text editor
  majorbot check         Test the settings without starting the bot
  majorbot start         Start everything (auto-restarts after crashes and reboots)
  majorbot stop          Stop everything
  majorbot restart       Restart (do this after changing .env)
  majorbot status        Is it running?
  majorbot logs          Show the live bot log (press Ctrl+C to leave)
  majorbot logs-web      Show the HTTPS proxy log (certificate / domain problems)
  majorbot update        Download the newest version and restart
  majorbot backup        Save a database backup into the backups folder
  majorbot restore FILE  Restore a backup file (replaces the current database!)
  majorbot help          Show this text

Text editor tips (nano): Ctrl+O then Enter saves, Ctrl+X exits.
EOF
}

require_env() {
  [[ -f .env ]] || die "Missing .env. Run: majorbot edit-config"
  local missing
  missing="$(grep -E '^(DOMAIN|DISCORD_TOKEN|DISCORD_GUILD_ID|DISCORD_CLIENT_ID|DISCORD_CLIENT_SECRET|TWITTERAPI_IO_KEY|POSTGRES_PASSWORD)=\s*$' .env | cut -d= -f1 || true)"
  if [[ -n "$missing" ]]; then
    die "These values in .env are still empty: $(echo "$missing" | tr '\n' ' '). Run: majorbot edit-config"
  fi
}

case "${1:-help}" in
  edit-config)
    [[ -f .env ]] || cp .env.example .env
    chmod 600 .env
    "${EDITOR:-nano}" .env
    say "Saved. Run 'majorbot check' to test, then 'majorbot restart' to apply."
    ;;
  check)
    require_env
    docker compose up -d db
    docker compose run --rm --no-deps app majors-lair-bot check
    ;;
  start)
    require_env
    docker compose up -d --build
    say "Started. Watch it with: majorbot logs"
    ;;
  stop)
    docker compose down
    say "Stopped."
    ;;
  restart)
    require_env
    docker compose up -d --build --force-recreate app caddy
    say "Restarted. Watch it with: majorbot logs"
    ;;
  status)
    docker compose ps
    ;;
  logs)
    docker compose logs --tail=200 -f app
    ;;
  logs-web)
    docker compose logs --tail=100 -f caddy
    ;;
  update)
    say "Downloading the newest version..."
    git pull --ff-only
    require_env
    docker compose up -d --build
    say "Updated and restarted. Watch it with: majorbot logs"
    ;;
  backup)
    mkdir -p backups
    file="backups/majorbot-$(date +%Y%m%d-%H%M%S).sql.gz"
    docker compose exec -T db pg_dump -U majorbot --clean --if-exists majorbot | gzip > "$file"
    ls -1t backups/majorbot-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
    say "Backup written to $INSTALL_DIR/$file (the newest 14 are kept)."
    ;;
  restore)
    [[ -n "${2:-}" && -f "${2}" ]] || die "Usage: majorbot restore backups/majorbot-YYYYMMDD-HHMMSS.sql.gz"
    say "Stopping the app while the database is restored..."
    docker compose stop app
    gunzip -c "$2" | docker compose exec -T db psql -q -U majorbot majorbot
    docker compose start app
    say "Restore finished. Watch it with: majorbot logs"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
