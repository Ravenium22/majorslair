"""Member registry: special-role flags and members without an X account.

Revision ID: 20260920_02
Revises: 20260822_01

The initial revision creates tables from the current ORM metadata, so on a fresh database
every column below already exists. Each step therefore checks the live schema first and
only changes what is missing, which keeps the migration safe on both fresh and existing
installs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_02"
down_revision = "20260822_01"
branch_labels = None
depends_on = None


def _user_columns() -> dict[str, dict]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"]: column for column in inspector.get_columns("users")}


def upgrade() -> None:
    columns = _user_columns()
    with op.batch_alter_table("users") as batch:
        if "special_role" not in columns:
            batch.add_column(
                sa.Column(
                    "special_role",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "special_role_names" not in columns:
            batch.add_column(
                sa.Column(
                    "special_role_names",
                    sa.String(length=255),
                    nullable=False,
                    server_default="",
                )
            )
        if "x_status" not in columns:
            batch.add_column(
                sa.Column("x_status", sa.String(length=32), nullable=False, server_default="")
            )
        if "x_checked_at" not in columns:
            batch.add_column(sa.Column("x_checked_at", sa.DateTime(timezone=True), nullable=True))
        for name in ("twitter_handle", "twitter_user_id"):
            if name in columns and not columns[name].get("nullable", True):
                batch.alter_column(name, existing_type=sa.String(length=32), nullable=True)

    index_names = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")}
    if "ix_users_special_role" not in index_names:
        op.create_index("ix_users_special_role", "users", ["special_role"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM users WHERE twitter_user_id IS NULL"))
    index_names = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")}
    if "ix_users_special_role" in index_names:
        op.drop_index("ix_users_special_role", table_name="users")
    columns = _user_columns()
    with op.batch_alter_table("users") as batch:
        for name in ("twitter_handle", "twitter_user_id"):
            if name in columns and columns[name].get("nullable", True):
                batch.alter_column(name, existing_type=sa.String(length=32), nullable=False)
        for name in ("x_checked_at", "x_status", "special_role_names", "special_role"):
            if name in columns:
                batch.drop_column(name)
