"""Add durable local job execution state.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("worker_id", sa.Text()))
    op.add_column("jobs", sa.Column("worker_pid", sa.Integer()))
    op.add_column("jobs", sa.Column("workload_pid", sa.Integer()))
    op.add_column("jobs", sa.Column("process_group_id", sa.Integer()))
    op.add_column("jobs", sa.Column("heartbeat_at", sa.Text()))
    op.add_column("jobs", sa.Column("cancel_requested_at", sa.Text()))
    op.add_column("jobs", sa.Column("exit_code", sa.Integer()))


def downgrade() -> None:
    op.drop_column("jobs", "exit_code")
    op.drop_column("jobs", "cancel_requested_at")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "process_group_id")
    op.drop_column("jobs", "workload_pid")
    op.drop_column("jobs", "worker_pid")
    op.drop_column("jobs", "worker_id")
