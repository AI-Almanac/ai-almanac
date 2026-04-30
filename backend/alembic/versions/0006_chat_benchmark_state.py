"""Add benchmark planning state to chat sessions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE chat_sessions
        ADD COLUMN IF NOT EXISTS benchmark_config JSONB,
        ADD COLUMN IF NOT EXISTS benchmark_validation JSONB,
        ADD COLUMN IF NOT EXISTS run_id TEXT
    """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE chat_sessions
        DROP COLUMN IF EXISTS run_id,
        DROP COLUMN IF EXISTS benchmark_validation,
        DROP COLUMN IF EXISTS benchmark_config
    """)
    )
