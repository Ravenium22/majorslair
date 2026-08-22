#!/bin/sh
set -eu

alembic upgrade head
exec majors-lair-bot
