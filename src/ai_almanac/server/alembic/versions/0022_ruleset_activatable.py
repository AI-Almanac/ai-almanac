"""Let a ruleset declare itself unfit to be the active default.

``activatable`` is ruleset content, authored in the packaged YAML: the
``unconstrained`` comparison control exists only to be one arm of an A/B and
says so in its header, but nothing stopped an admin clicking Activate and
making it every user's assistant. Default TRUE — every existing ruleset keeps
its current standing, and packaged reseeding rewrites the column from YAML
like the other content columns.

Additive only; a rollback runs against this schema unchanged, treating the
column as absent.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_rulesets",
        sa.Column("activatable", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("assistant_rulesets", "activatable")
