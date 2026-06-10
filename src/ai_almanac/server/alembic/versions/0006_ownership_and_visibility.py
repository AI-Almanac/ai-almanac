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


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    # data_sources: ownership + visibility + how the source is backed.
    _add_column_if_missing("data_sources", sa.Column("owner_id", sa.Text(), nullable=True))
    _add_column_if_missing(
        "data_sources",
        sa.Column(
            "visibility", sa.Text(), nullable=False, server_default="shared"
        ),  # private | shared
    )
    _add_column_if_missing(
        "data_sources",
        sa.Column(
            "origin", sa.Text(), nullable=False, server_default="mounted"
        ),  # mounted | upload
    )
    _create_index_if_missing("idx_data_sources_owner", "data_sources", ["owner_id"])

    # jobs: visibility + the runner that produced them and its opaque handle.
    _add_column_if_missing(
        "jobs",
        sa.Column(
            "visibility", sa.Text(), nullable=False, server_default="private"
        ),  # private | shared
    )
    _add_column_if_missing("jobs", sa.Column("runner", sa.Text(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("runner_handle", sa.JSON(), nullable=True))
    _create_index_if_missing("idx_jobs_visibility", "jobs", ["visibility"])

    # users: human-readable display name parsed from the identity headers.
    _add_column_if_missing("users", sa.Column("display_name", sa.Text(), nullable=True))


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
