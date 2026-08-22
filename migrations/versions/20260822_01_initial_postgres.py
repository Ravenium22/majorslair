"""Initial PostgreSQL control-center schema.

Revision ID: 20260822_01
Revises: None
"""
from __future__ import annotations

from alembic import op

from majors_lair_bot.orm import Base

revision = "20260822_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
