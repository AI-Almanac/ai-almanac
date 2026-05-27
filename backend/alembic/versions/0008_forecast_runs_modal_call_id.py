"""Add modal_call_id to forecast_runs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-18
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS modal_call_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE forecast_runs DROP COLUMN IF EXISTS modal_call_id")
