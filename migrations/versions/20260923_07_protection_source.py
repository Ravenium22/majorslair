"""Split protection into "set by hand" and "granted by a Discord role".

Revision ID: 20260923_07
Revises: 20260921_06

Sync from Discord could only ever add protection: a member who lost the role stayed
protected forever, because the sync passed "no change" instead of "no longer protected".
Telling the two sources apart is what lets sync mirror the server without discarding a
decision an admin made by hand.

The backfill is not a guess. Sync wrote the matching role names into special_role_names,
so a protected member whose names are all configured role names was protected by sync, and
that protection moves to the column sync now owns. Everything else is treated as manual and
is left exactly as it is.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_07"
down_revision = "20260921_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    with op.batch_alter_table("users") as batch:
        if "special_role_manual" not in columns:
            batch.add_column(
                sa.Column(
                    "special_role_manual",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "role_protected_names" not in columns:
            batch.add_column(
                sa.Column(
                    "role_protected_names",
                    sa.String(255),
                    nullable=False,
                    server_default="",
                )
            )
    if "special_role_manual" in columns and "role_protected_names" in columns:
        return  # already backfilled by an earlier run

    configured_row = bind.execute(
        sa.text("SELECT value FROM config WHERE key = 'protected_role_names'")
    ).first()
    configured = {
        name.strip().lower()
        for name in (configured_row[0] if configured_row else "").split(",")
        if name.strip()
    }
    rows = bind.execute(
        sa.text(
            "SELECT discord_user_id, special_role, special_role_names FROM users "
            "WHERE special_role = :yes"
        ),
        {"yes": True},
    ).fetchall()
    for discord_user_id, _protected, names in rows:
        held = [part.strip() for part in (names or "").split(",") if part.strip()]
        from_sync = bool(held) and all(part.lower() in configured for part in held)
        if from_sync:
            bind.execute(
                sa.text(
                    "UPDATE users SET role_protected_names = :names, special_role_names = '', "
                    "special_role_manual = :no WHERE discord_user_id = :id"
                ),
                {"names": ", ".join(held)[:255], "no": False, "id": discord_user_id},
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE users SET special_role_manual = :yes WHERE discord_user_id = :id"
                ),
                {"yes": True, "id": discord_user_id},
            )


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    with op.batch_alter_table("users") as batch:
        if "role_protected_names" in columns:
            batch.drop_column("role_protected_names")
        if "special_role_manual" in columns:
            batch.drop_column("special_role_manual")
