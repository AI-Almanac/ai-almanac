"""Follow-ups, analysis names, and what started and informed each assistant turn.

``follow_ups`` queues assistant work until a workspace's jobs finish.
``workspace_analyses`` holds the short name of the files one chat turn made,
from the assistant or the user. Turn logs gain ``trigger`` (``user`` or
``follow_up:<id>``) and ``read_artifact_ids``, the artifacts the turn's tools
read, so a number quoted in chat is attributable to the files it came from.

Additive only: a new table and nullable columns that old code never reads, so
a rollback onto this schema runs unchanged.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "follow_ups",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("job_ids", sa.JSON(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
    )
    op.create_index("ix_follow_ups_workspace", "follow_ups", ["workspace_id"])
    op.create_index("ix_follow_ups_status", "follow_ups", ["status"])
    op.create_table(
        "workspace_analyses",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("named_by", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "turn_id"),
    )
    op.add_column("assistant_turn_logs", sa.Column("trigger", sa.Text(), nullable=True))
    op.add_column("assistant_turn_logs", sa.Column("read_artifact_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_turn_logs", "read_artifact_ids")
    op.drop_column("assistant_turn_logs", "trigger")
    op.drop_table("workspace_analyses")
    op.drop_index("ix_follow_ups_status", table_name="follow_ups")
    op.drop_index("ix_follow_ups_workspace", table_name="follow_ups")
    op.drop_table("follow_ups")
