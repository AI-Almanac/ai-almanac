"""Mark a chat session as belonging to a ruleset comparison.

A side-by-side comparison runs each variant in its own cloned session, so the
config-mutating tools work and two proposed configurations can be compared. Those
sessions are scratch: they carry a ``comparison_id`` and are hidden from the
user's session list, which is also how the vote finds the turns to rate.

One nullable column rather than a separate ``is_scratch`` flag —
``comparison_id IS NOT NULL`` already says the same thing, and two columns that
must agree eventually disagree.

Additive only; a rollback runs against this schema unchanged, treating the column
as absent.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("comparison_id", sa.Text(), nullable=True))
    op.create_index("ix_chat_sessions_comparison", "chat_sessions", ["comparison_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_comparison", table_name="chat_sessions")
    op.drop_column("chat_sessions", "comparison_id")
