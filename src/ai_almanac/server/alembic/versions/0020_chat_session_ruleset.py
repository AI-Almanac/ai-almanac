"""Let a chat session pin the ruleset it runs under.

Null means "follow the active ruleset" — the behaviour every existing session
keeps. A non-null value is a user's per-session choice; if that ruleset is
later archived or deleted, resolution silently falls back to the active one,
so this is a preference, not a foreign key.

Additive only; a rollback runs against this schema unchanged, treating the
column as absent.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("ruleset_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "ruleset_id")
