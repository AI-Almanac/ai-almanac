"""Analyses a user hid from a workspace's products list.

``workspace_hidden_analyses`` holds one row per hidden analysis (the files one
chat turn made). Hiding only leaves the analysis out of the list: its files stay
in the workspace and the ledger, since other results may have been made from them.

Additive only: a new table old code never reads, so a rollback onto this schema
runs unchanged (hidden analyses simply show again).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_hidden_analyses",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("hidden_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "turn_id"),
    )


def downgrade() -> None:
    op.drop_table("workspace_hidden_analyses")
