"""Re-date logged retweets to their post's time.

Revision ID: 20260921_03
Revises: 20260920_02

Retweets carry no timestamp on X, and earlier scans dated them at scan time. A long
scan therefore stacked every retweet a member made onto a single day, which tripped
the per-day scoring cap and zeroed most of them. This one-off moves each retweet row
to its source post's own publish time (never later than the row's current date), so
the next rescore spreads them across the real days.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_03"
down_revision = "20260920_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    posts = {
        row[0]: row[1]
        for row in bind.execute(
            sa.text("SELECT tweet_id, post_created_at FROM tracked_posts")
        ).fetchall()
        if row[1] is not None
    }
    rows = bind.execute(
        sa.text(
            "SELECT action_key, source_post_id, occurred_at FROM actions_log "
            "WHERE action_type = 'retweet'"
        )
    ).fetchall()
    update = sa.text("UPDATE actions_log SET occurred_at = :when WHERE action_key = :key")
    for action_key, source_post_id, occurred_at in rows:
        post_time = posts.get(source_post_id)
        if post_time is None or occurred_at is None:
            continue
        if post_time < occurred_at:
            bind.execute(update, {"when": post_time, "key": action_key})


def downgrade() -> None:
    # The original scan-time dates are not recoverable; nothing to undo safely.
    pass
