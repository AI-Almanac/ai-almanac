"""Persistent settings overlay.

Adds ``app_config`` (key/value) so settings written from the admin Settings UI
persist in the database instead of the container's ephemeral ``config.yaml``,
which is wiped on every redeploy.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_config")
