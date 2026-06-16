"""Shared-host identity, uploads, LLM profiles, audit, usage, and job metadata.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("issuer", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("groups", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_login_at", sa.Text()),
    ):
        op.add_column("users", column)

    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("data_source_id", sa.Text()),
        sa.Column("expected_filename", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("max_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("checksum", sa.Text()),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("grant_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
    )
    op.create_index("idx_upload_sessions_owner", "upload_sessions", ["owner_id"])

    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider_type", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "user_llm_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider_id", sa.Text(), sa.ForeignKey("llm_providers.id"), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "provider_id", "model_name"),
    )
    op.create_index("idx_llm_profiles_user", "user_llm_profiles", ["user_id"])

    for table_name in ("audit_events", "usage_events"):
        op.create_table(
            table_name,
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("user_id", sa.Text()),
            sa.Column("event_type", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text()),
            sa.Column("resource_id", sa.Text()),
            sa.Column("quantity", sa.BigInteger()),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index(f"idx_{table_name}_user_created", table_name, ["user_id", "created_at"])

    for column in (
        sa.Column("runner_request", sa.JSON()),
        sa.Column("timeout_seconds", sa.Integer()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_category", sa.Text()),
    ):
        op.add_column("jobs", column)


def downgrade() -> None:
    for name in ("failure_category", "attempt_count", "timeout_seconds", "runner_request"):
        op.drop_column("jobs", name)
    for table_name in ("usage_events", "audit_events"):
        op.drop_index(f"idx_{table_name}_user_created", table_name=table_name)
        op.drop_table(table_name)
    op.drop_index("idx_llm_profiles_user", table_name="user_llm_profiles")
    op.drop_table("user_llm_profiles")
    op.drop_table("llm_providers")
    op.drop_index("idx_upload_sessions_owner", table_name="upload_sessions")
    op.drop_table("upload_sessions")
    for name in ("last_login_at", "groups", "status", "subject", "issuer"):
        op.drop_column("users", name)
