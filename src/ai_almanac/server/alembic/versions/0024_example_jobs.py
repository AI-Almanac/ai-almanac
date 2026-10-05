"""Per-user hiding of example jobs.

Adds ``user_hidden_jobs``. Admins can promote a completed job to
``visibility='example'`` so every user sees it in their lists; "deleting" an
example only records a hide row here, keeping the single shared copy of the
job and its artifacts intact for everyone else.

Additive only; old code never reads the table, so a rollback onto this schema
runs unchanged.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_hidden_jobs",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "job_id"),
    )


def downgrade() -> None:
    op.drop_table("user_hidden_jobs")
