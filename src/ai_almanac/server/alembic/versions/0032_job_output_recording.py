"""Track recording a finished job's files into the ledger and its workspaces.

Recording now runs in a loop on every server instance, apart from publication.
``outputs_recording_at`` is a lease: the instance that sets it records the job,
and another takes over only once it is stale. ``outputs_recorded_at`` says the
files reached every workspace linking the job, so a recording interrupted after
the ledger step completed is still picked up again.

Jobs already published are marked recorded, unless their ledger step is still
open (a recording that never finished), so the loop does not re-add files to
existing workspaces.

Old code on this schema stays correct: both columns are nullable and unread by it.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("outputs_recording_at", sa.Text()))
    op.add_column("jobs", sa.Column("outputs_recorded_at", sa.Text()))
    op.execute(
        "UPDATE jobs SET outputs_recorded_at = artifacts_published_at "
        "WHERE artifacts_published_at IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM provenance_steps WHERE provenance_steps.job_id = jobs.id "
        "AND provenance_steps.completed_at IS NULL)"
    )


def downgrade() -> None:
    op.drop_column("jobs", "outputs_recorded_at")
    op.drop_column("jobs", "outputs_recording_at")
