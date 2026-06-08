"""Data sources catalog (v0).

A minimal registry of user-configurable obs/model directories, replacing the
env-var-driven YAML registry for runtime use. The YAML files in `server/config/`
still ship with the package and are used as a seed on first launch (so testdata
works without setup), but the data_sources table is the runtime source of truth.

This is the v0 of what will grow into the full artifact catalog. Kept narrow
on purpose — schema-light, no provenance, no tags, no run linkage. Those land
when we build out the catalog properly.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),  # 'obs' | 'model'
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("region", sa.Text()),  # required for 'model', nullable for 'obs'
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_data_sources_kind", "data_sources", ["kind"])
    op.create_index("idx_data_sources_region", "data_sources", ["region"])


def downgrade() -> None:
    op.drop_index("idx_data_sources_region", table_name="data_sources")
    op.drop_index("idx_data_sources_kind", table_name="data_sources")
    op.drop_table("data_sources")
