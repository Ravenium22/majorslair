#!/usr/bin/env bash
# One-time server setup for the Major's Lair engagement bot + admin website.
#
# Run on a fresh Ubuntu server (Hetzner Cloud, Ubuntu 22.04 or 24.04) as root:
#
#   curl -fsSL https://raw.githubusercontent.com/Ravenium22/majorslair/main/deploy/install.sh | bash -s -- https://github.com/Ravenium22/majorslair.git
#
# What it does:
#   1. Installs git, curl, nano and Docker (with the compose plugin).
#   2. Downloads the code into /opt/majorbot.
#   3. Creates .env from the template and generates a database password.
#   4. Installs the `majorbot` helper command (start / stop / logs / update / backup ...).
#   5. Schedules a daily database backup at 04:00 server time.
#
# It is safe to run again; it only fills in what is missing.

set -euo pipefail

REPO_URL="${1:-}"
INSTALL_DIR="/opt/majorbot"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run this as root (log in as root, or prefix with sudo)."
command -v apt-get >/dev/null 2>&1 || die "This script expects Ubuntu/Debian (apt-get not found)."

if [[ -z "$REPO_URL" && ! -d "$INSTALL_DIR/.git" ]]; then
  die "Pass the git URL of the bot code, e.g.: bash install.sh https://github.com/Ravenium22/majorslair.git"
fi

say "Installing system packages (git, curl, nano, openssl)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl nano openssl ca-certificates >/dev/null

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (this takes a minute)..."
  curl -fsSL https://get.docker.com | sh >/dev/null
else
  say "Docker is already installed."
fi
if ! docker compose version >/dev/null 2>&1; then
  say "Installing the Docker Compose plugin..."
  apt-get install -y -qq docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true

if [[ -d "$INSTALL_DIR/.git" ]]; then
  say "Code already present in $INSTALL_DIR; fetching the latest version..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  say "Downloading the code into $INSTALL_DIR..."
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
mkdir -p backups
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  say "Created $INSTALL_DIR/.env from the template. You must fill it in next."
else
  say ".env already exists; leaving it untouched."
fi
if grep -Eq '^POSTGRES_PASSWORD=\s*$' .env; then
  password="$(openssl rand -hex 24)"
  sed -i "s/^POSTGRES_PASSWORD=\s*$/POSTGRES_PASSWORD=${password}/" .env
  say "Generated a database password in .env."
fi

chmod +x deploy/bot.sh
ln -sf "$INSTALL_DIR/deploy/bot.sh" /usr/local/bin/majorbot
say "Installed the 'majorbot' command."

cron_line="0 4 * * * /usr/local/bin/majorbot backup >/var/log/majorbot-backup.log 2>&1"
if ! crontab -l 2>/dev/null | grep -Fq "majorbot backup"; then
  (crontab -l 2>/dev/null || true; echo "$cron_line") | crontab -
  say "Scheduled a daily database backup (04:00 server time) into $INSTALL_DIR/backups."
fi

say "Building the application image (first build takes a few minutes)..."
docker compose build --quiet app

cat <<'EOF'

=====================================================================
 Installation finished. Next steps (see docs/HETZNER_GUIDE.md):

   majorbot edit-config     -> fill in the domain, Discord values, API key
   majorbot check           -> test everything without starting the bot
   majorbot start           -> start the bot + website (auto-restarts on reboot)
   majorbot logs            -> watch what the bot is doing
=====================================================================
EOF
