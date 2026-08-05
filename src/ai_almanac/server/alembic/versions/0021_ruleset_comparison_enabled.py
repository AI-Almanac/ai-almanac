"""Let an admin choose which rulesets users can see and compare.

``comparison_enabled`` is deployment state, like ``is_active``, not ruleset
content: it gates what the style picker offers, what a session may pin, and
which arms a user comparison may run. Default FALSE — exposure is an explicit
admin action, and packaged reseeding preserves the admin's choice by never
touching the column on update.

This replaces the single ``assistant_comparison_candidate`` setting: with
user-chosen pairs there is no distinguished candidate, only an allow-list.

Additive only; a rollback runs against this schema unchanged, treating the
column as absent.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_rulesets",
        sa.Column("comparison_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assistant_rulesets", "comparison_enabled")
