"""Add executable source metadata.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column(
            "location_type",
            sa.Text(),
            nullable=False,
            server_default="local_directory",
        ),
    )
    op.add_column(
        "data_sources",
        sa.Column("status", sa.Text(), nullable=False, server_default="invalid"),
    )
    op.add_column("data_sources", sa.Column("validation_error", sa.Text()))
    op.add_column("data_sources", sa.Column("updated_at", sa.Text()))


def downgrade() -> None:
    op.drop_column("data_sources", "updated_at")
    op.drop_column("data_sources", "validation_error")
    op.drop_column("data_sources", "status")
    op.drop_column("data_sources", "location_type")
