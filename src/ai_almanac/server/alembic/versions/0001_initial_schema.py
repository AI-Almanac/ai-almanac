"""Initial schema.

Collapsed from the eight pre-rearchitecture migrations into one SQLite-native
baseline. Cloud-only Postgres-isms (JSONB, TIMESTAMPTZ, `ADD COLUMN IF NOT
EXISTS`) are dropped in favor of portable SQLAlchemy types so the same
migration runs on SQLite (default) and Postgres (public deploys behind
oauth2-proxy).

Revision ID: 0001
Revises:
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("external_id", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "datasets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("storage_key", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("ready_at", sa.Text()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        # dataset_id intentionally has no FK — demo datasets are config-driven
        # and not stored in the `datasets` table, so an FK would IntegrityError
        # on every demo job submission.
        sa.Column("dataset_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("config_json", sa.Text()),
        sa.Column("run_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("metrics_cache", sa.JSON()),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("provider_state", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("transcript", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("benchmark_config", sa.JSON()),
        sa.Column("benchmark_validation", sa.JSON()),
        sa.Column("run_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "chat_artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("init_time", sa.Text()),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("lead_hours", sa.JSON(), nullable=False),
        sa.Column("storage_prefix", sa.Text(), nullable=False),
        sa.Column("manifest_key", sa.Text()),
        sa.Column("config_json", sa.JSON()),
        sa.Column("modal_call_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("error", sa.Text()),
    )

    # Hot-path indexes.
    op.create_index("idx_jobs_user_id", "jobs", ["user_id"])
    op.create_index("idx_jobs_status", "jobs", ["status"])
    op.create_index("idx_jobs_run_id", "jobs", ["run_id"])
    op.create_index("idx_datasets_user_id", "datasets", ["user_id"])
    op.create_index(
        "idx_forecast_runs_user_created",
        "forecast_runs",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_forecast_runs_user_created", table_name="forecast_runs")
    op.drop_index("idx_datasets_user_id", table_name="datasets")
    op.drop_index("idx_jobs_run_id", table_name="jobs")
    op.drop_index("idx_jobs_status", table_name="jobs")
    op.drop_index("idx_jobs_user_id", table_name="jobs")
    op.drop_table("forecast_runs")
    op.drop_table("chat_artifacts")
    op.drop_table("chat_sessions")
    op.drop_table("jobs")
    op.drop_table("datasets")
    op.drop_table("users")
