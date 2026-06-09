"""Ownership and visibility for catalog and jobs.

Additive columns that make data sources and jobs ownable and shareable for the
shared (multi-user) deployment. Personal installs keep working unchanged:
existing rows take the server defaults (built-in sources are shared+mounted,
jobs are private), and `owner_id` is NULL for built-in/operator-global rows.

`origin` is distinct from the existing `location_type` column (which records
how a source is physically backed, e.g. `local_directory`): `origin` records
whether the source is an admin-managed mounted path or a user upload.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # data_sources: ownership + visibility + how the source is backed.
    op.add_column("data_sources", sa.Column("owner_id", sa.Text(), nullable=True))
    op.add_column(
        "data_sources",
        sa.Column(
            "visibility", sa.Text(), nullable=False, server_default="shared"
        ),  # private | shared
    )
    op.add_column(
        "data_sources",
        sa.Column(
            "origin", sa.Text(), nullable=False, server_default="mounted"
        ),  # mounted | upload
    )
    op.create_index("idx_data_sources_owner", "data_sources", ["owner_id"])

    # jobs: visibility + the runner that produced them and its opaque handle.
    op.add_column(
        "jobs",
        sa.Column(
            "visibility", sa.Text(), nullable=False, server_default="private"
        ),  # private | shared
    )
    op.add_column("jobs", sa.Column("runner", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("runner_handle", sa.JSON(), nullable=True))
    op.create_index("idx_jobs_visibility", "jobs", ["visibility"])

    # users: human-readable display name parsed from the identity headers.
    op.add_column("users", sa.Column("display_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
    op.drop_index("idx_jobs_visibility", table_name="jobs")
    op.drop_column("jobs", "runner_handle")
    op.drop_column("jobs", "runner")
    op.drop_column("jobs", "visibility")
    op.drop_index("idx_data_sources_owner", table_name="data_sources")
    op.drop_column("data_sources", "origin")
    op.drop_column("data_sources", "visibility")
    op.drop_column("data_sources", "owner_id")
