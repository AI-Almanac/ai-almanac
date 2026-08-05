"""Assistant rulesets.

Adds ``assistant_rulesets``, which turns the assistant's policy from Python
string constants plus one wholesale ``chat_system_prompt`` override into named,
versioned rows an admin can clone and edit at runtime. Packaged YAML in
``server/config/rulesets/`` seeds the defaults.

Additive only: existing code ignores the table, and the prompt falls back to the
packaged ``builtin`` ruleset when no row is active, so a rollback onto this
schema runs unchanged.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_rulesets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        # "packaged" rows are reseeded from YAML on startup; "custom" rows are
        # admin-authored and never overwritten.
        sa.Column("source", sa.Text(), nullable=False, server_default="custom"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prompt_sections", sa.JSON(), nullable=False),
        sa.Column("tool_policy", sa.JSON(), nullable=False),
        # No guardrail thresholds here on purpose. They decide what the platform
        # accepts, so they live in the settings overlay where the submission
        # chokepoint reads them too — see services.guardrails.current(). On a
        # ruleset they could drift from the enforced value.
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("model_settings", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    # At most one active ruleset. A partial unique index expresses that directly
    # rather than leaving it to application code to keep straight.
    op.create_index(
        "ix_assistant_rulesets_active",
        "assistant_rulesets",
        ["is_active"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_rulesets_active", table_name="assistant_rulesets")
    op.drop_table("assistant_rulesets")
