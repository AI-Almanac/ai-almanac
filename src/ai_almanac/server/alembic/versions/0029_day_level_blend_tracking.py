"""Track a blend's day-level blend, which now trains in its own Modal call.

run_blend spawns run_day_level_blend and finishes the week-level blend first, so
the job turns complete while the day-level blend may still be training. These
columns follow that call: its id, its state, why it failed, when it finished,
and when its outputs were indexed (a second claim after artifacts_published_at).

Old code on this schema stays correct: every column is nullable and unread by it.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_COLUMNS = (
    "day_level_call_id",
    "day_level_status",
    "day_level_error",
    "day_level_completed_at",
    "day_level_published_at",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("jobs", sa.Column(name, sa.Text()))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("jobs", name)
