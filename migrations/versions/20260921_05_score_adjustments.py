"""Add score_adjustments (manual points and transfers).

Revision ID: 20260921_05
Revises: 20260921_04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_05"
down_revision = "20260921_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "score_adjustments" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "score_adjustments",
        sa.Column("adjustment_id", sa.String(36), primary_key=True),
        sa.Column("cycle_id", sa.String(64), nullable=False, index=True),
        sa.Column("discord_user_id", sa.String(32), nullable=False, index=True),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("actor_discord_id", sa.String(32), nullable=False),
        sa.Column("counterpart_discord_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("transfer_id", sa.String(36), nullable=False, server_default="", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    if "score_adjustments" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("score_adjustments")
