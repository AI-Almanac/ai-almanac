"""Add indexes on high-cardinality filter columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-20
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id     ON jobs(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_run_id      ON jobs(run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_datasets_user_id ON datasets(user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_jobs_user_id")
    op.execute("DROP INDEX IF EXISTS idx_jobs_status")
    op.execute("DROP INDEX IF EXISTS idx_jobs_run_id")
    op.execute("DROP INDEX IF EXISTS idx_datasets_user_id")
