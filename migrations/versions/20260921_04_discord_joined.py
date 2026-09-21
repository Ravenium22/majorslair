"""Add users.discord_joined_at (Discord server join date).

Revision ID: 20260921_04
Revises: 20260921_03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_04"
down_revision = "20260921_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "discord_joined_at" not in columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("discord_joined_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "discord_joined_at" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("discord_joined_at")
