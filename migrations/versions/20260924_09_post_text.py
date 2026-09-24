"""Store the text of tracked posts.

Revision ID: 20260924_09
Revises: 20260923_08

The tracked posts list showed each post as a date and a 19-digit id. Existing rows start
empty and fill in the next time a scan reads the post.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_09"
down_revision = "20260923_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("tracked_posts")}
    if "text" not in columns:
        with op.batch_alter_table("tracked_posts") as batch:
            batch.add_column(sa.Column("text", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("tracked_posts")}
    if "text" in columns:
        with op.batch_alter_table("tracked_posts") as batch:
            batch.drop_column("text")
