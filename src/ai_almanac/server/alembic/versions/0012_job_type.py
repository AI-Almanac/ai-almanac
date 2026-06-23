"""Discriminate job kinds.

Adds ``jobs.job_type`` so blend jobs (and future kinds) are submitted, listed,
and routed distinctly from ROMP benchmark jobs. Existing rows are benchmarks.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "job_type", sa.Text(), nullable=False, server_default="benchmark"
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "job_type")
