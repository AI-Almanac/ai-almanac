"""Indexed job artifacts.

Records the published outputs of a completed job (metrics, figures, logs) with
enough metadata to authorize downloads and stream files without re-walking the
workspace. Storage keys are opaque so the filesystem layout never leaks to
routers. Artifacts inherit their job's visibility; deleting a job cascades.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),  # metric | figure | log | output
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),  # sha256
        sa.Column("storage_key", sa.Text(), nullable=False),  # opaque
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_job_artifacts_job_id", "job_artifacts", ["job_id"])


def downgrade() -> None:
    op.drop_index("idx_job_artifacts_job_id", table_name="job_artifacts")
    op.drop_table("job_artifacts")
