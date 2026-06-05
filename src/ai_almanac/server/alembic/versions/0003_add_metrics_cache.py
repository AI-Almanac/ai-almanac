"""Add metrics_cache column to jobs table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS metrics_cache JSONB"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE jobs DROP COLUMN IF EXISTS metrics_cache"))
