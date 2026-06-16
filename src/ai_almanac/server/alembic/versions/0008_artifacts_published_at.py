"""Track artifact publication on jobs.

Marks when a completed job's outputs were indexed into job_artifacts so the
reconciler publishes each job exactly once (NULL = not yet published).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("artifacts_published_at", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "artifacts_published_at")
