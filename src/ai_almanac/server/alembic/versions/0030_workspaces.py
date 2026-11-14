"""Provenance ledger and workspaces.

The ledger is per user: ``artifacts`` are content-addressed files,
``provenance_steps`` records one step per real execution (a primitive call or
a job submission), and ``approvals`` records the human decision behind each
gated step. A workspace owns no provenance; ``workspace_items`` is the set of
artifacts and jobs a project has pulled in, with its own labels and titles.
``workspaces.settings_json`` holds a ``WorkspaceSettings`` document (see
``services/workspace_settings.py``); NULL means no settings.

Additive only: new tables, no changes to existing ones. Old code never reads
them, so a rollback onto this schema runs unchanged.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("settings_json", sa.Text()),
    )
    op.create_index("ix_workspaces_user", "workspaces", ["user_id"])

    op.create_table(
        "workspace_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("item_kind", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text()),
        sa.Column("job_id", sa.Text()),
        sa.Column("label", sa.Text()),
        sa.Column("added_at", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("supporting", sa.Boolean()),
        sa.Column("added_by", sa.Text()),
        sa.UniqueConstraint("workspace_id", "artifact_id", name="uq_workspace_items_artifact"),
        sa.UniqueConstraint("workspace_id", "job_id", name="uq_workspace_items_job"),
        sa.UniqueConstraint("workspace_id", "label", name="uq_workspace_items_label"),
    )
    op.create_index("ix_workspace_items_job", "workspace_items", ["job_id"])

    op.create_table(
        "provenance_steps",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("primitive", sa.Text(), nullable=False),
        sa.Column("primitive_version", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("runtime", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.Text()),
        sa.Column("code_hash", sa.Text()),
        sa.Column("turn_id", sa.Text()),
        sa.Column("session_id", sa.Text()),
        sa.Column("ruleset_id", sa.Text()),
        sa.Column("ruleset_version", sa.Integer()),
        sa.Column("model_name", sa.Text()),
        sa.Column("approval_id", sa.Text()),
        sa.Column("parent_step_ids", sa.JSON(), nullable=False),
        sa.Column("step_hash", sa.Text(), nullable=False),
    )
    op.create_index("ix_provenance_steps_user", "provenance_steps", ["user_id"])
    op.create_index("ix_provenance_steps_job", "provenance_steps", ["job_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("location_kind", sa.Text(), nullable=False),
        sa.Column("path", sa.Text()),
        sa.Column("job_id", sa.Text()),
        sa.Column("job_file_kind", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("provenance_step_id", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_artifacts_user_content"),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text()),
        sa.Column("turn_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("approvals")
    op.drop_table("artifacts")
    op.drop_index("ix_provenance_steps_job", table_name="provenance_steps")
    op.drop_index("ix_provenance_steps_user", table_name="provenance_steps")
    op.drop_table("provenance_steps")
    op.drop_index("ix_workspace_items_job", table_name="workspace_items")
    op.drop_table("workspace_items")
    op.drop_index("ix_workspaces_user", table_name="workspaces")
    op.drop_table("workspaces")
