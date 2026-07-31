"""Per-turn assistant telemetry.

Adds ``assistant_turn_logs``. Until now a transcript could not be attributed to
a prompt revision or a model: ``chat_sessions.transcript`` is a JSON blob with
no provenance, and the ``llm.request`` usage event carried only latency and an
exception class name. Iterating on rulesets needs the opposite — every turn
recorded against the ruleset and model that produced it, with the tool calls it
made and the guardrails that fired.

Additive only; nothing reads the table to serve a chat turn, so a rollback onto
this schema runs unchanged.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_turn_logs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        # Provenance: which policy and which model produced this answer.
        sa.Column("ruleset_id", sa.Text(), nullable=True),
        sa.Column("ruleset_version", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("scope_kind", sa.Text(), nullable=True),
        # Cost and reliability.
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=True),
        sa.Column("failure_category", sa.Text(), nullable=True),
        # Behaviour: tool names called, guardrail rule keys that fired, and the
        # derived compliance flags (services/turn_log.py).
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("guardrail_keys", sa.JSON(), nullable=False),
        sa.Column("flags", sa.JSON(), nullable=False),
        # Set when the turn came from a side-by-side comparison run (phase 4).
        sa.Column("comparison_id", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("rating_note", sa.Text(), nullable=True),
    )
    # Ratings arrive by (session, turn) from the client, and the ruleset
    # comparison reads by ruleset.
    op.create_index(
        "ix_assistant_turn_logs_turn",
        "assistant_turn_logs",
        ["session_id", "turn_id"],
        unique=True,
    )
    op.create_index("ix_assistant_turn_logs_ruleset", "assistant_turn_logs", ["ruleset_id"])


def downgrade() -> None:
    op.drop_index("ix_assistant_turn_logs_ruleset", table_name="assistant_turn_logs")
    op.drop_index("ix_assistant_turn_logs_turn", table_name="assistant_turn_logs")
    op.drop_table("assistant_turn_logs")
