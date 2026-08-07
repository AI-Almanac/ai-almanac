"""Let admins preview a ruleset without exposing it to users.

``admin_enabled`` is deployment state, like ``comparison_enabled``: exposure
of the ruleset to admins only, so a draft can be pinned to real sessions and
compared before any user sees it. A ruleset is selectable when it is exposed
to users, or when the requester is an admin and this flag is set.

Additive only; a rollback runs against this schema unchanged, treating the
column as absent — admin-preview rulesets simply become invisible, which is
the safe direction.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_rulesets",
        sa.Column("admin_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assistant_rulesets", "admin_enabled")
