"""Deduplicate job_artifacts and make (job_id, kind, filename) unique.

Concurrent reconciler loops on multiple instances could each index the same
completed job, leaving every artifact listed twice. Keeps one row per file.

Old code on this schema stays correct: a racing duplicate insert now fails its
transaction instead of committing, and the winning instance's rows stand.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-05
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM job_artifacts WHERE id NOT IN ("
        "SELECT MIN(id) FROM job_artifacts GROUP BY job_id, kind, filename)"
    )
    op.create_index(
        "uq_job_artifacts_job_kind_filename",
        "job_artifacts",
        ["job_id", "kind", "filename"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_job_artifacts_job_kind_filename", table_name="job_artifacts")
