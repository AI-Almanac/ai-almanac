"""Per-session blend configuration.

Adds ``chat_sessions.blend_config`` / ``blend_validation`` so one chat session
can carry a blend setup alongside its benchmark setup as the user flows from
benchmarking into blending. Mirrors the existing ``benchmark_config`` columns.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("blend_config", sa.JSON()))
    op.add_column("chat_sessions", sa.Column("blend_validation", sa.JSON()))


def downgrade() -> None:
    op.drop_column("chat_sessions", "blend_validation")
    op.drop_column("chat_sessions", "blend_config")
