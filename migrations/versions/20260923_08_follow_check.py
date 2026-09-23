"""Record whether each member follows the tracked accounts.

Revision ID: 20260923_08
Revises: 20260923_07

Empty means never checked, which is deliberately different from "does not follow": the
follow check can only prove a negative when it read the whole follower list.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_08"
down_revision = "20260923_07"
branch_labels = None
depends_on = None

COLUMNS = {
    "follows_primary": sa.Column(
        "follows_primary", sa.String(8), nullable=False, server_default=""
    ),
    "follows_secondary": sa.Column(
        "follows_secondary", sa.String(8), nullable=False, server_default=""
    ),
    "follows_checked_at": sa.Column("follows_checked_at", sa.DateTime(timezone=True)),
}


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    with op.batch_alter_table("users") as batch:
        for name, column in COLUMNS.items():
            if name not in existing:
                batch.add_column(column)


def downgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    with op.batch_alter_table("users") as batch:
        for name in COLUMNS:
            if name in existing:
                batch.drop_column(name)
