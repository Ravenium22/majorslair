"""Raise max_mention_pages from the old default of 10 to 50 where it was never changed.

Revision ID: 20260921_06
Revises: 20260921_05

Ten pages (about 200 mentions) covered only a few days for the tracked accounts, so
shout-outs older than that were silently skipped by every normal scan.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_06"
down_revision = "20260921_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE config SET value = '50' WHERE key = 'max_mention_pages' AND value = '10'")
    )


def downgrade() -> None:
    pass
