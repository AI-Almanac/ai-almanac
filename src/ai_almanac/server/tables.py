"""SQLAlchemy Core table metadata for query building.

Alembic owns the schema (``server/alembic/versions``); these definitions
mirror it so queries are schema-checked at import time and dialect
differences (JSON binds, expanding IN, FOR UPDATE) compile away instead of
being branched at call sites. Never ``create_all`` from here.

Column types follow the migrations. ``config_json`` is deliberately Text —
call sites own its serialization — while ``sa.JSON`` columns round-trip
Python objects on both SQLite and PostgreSQL when accessed through these
tables. Raw ``text()`` queries bypass that result processing, so a column
read through both paths must tolerate either shape.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("external_id", sa.Text(), nullable=False, unique=True),
    sa.Column("email", sa.Text()),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("display_name", sa.Text()),
    sa.Column("issuer", sa.Text(), nullable=False, server_default=""),
    sa.Column("subject", sa.Text(), nullable=False, server_default=""),
    sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    sa.Column("groups", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("last_login_at", sa.Text()),
    sa.Column("llm_preference", sa.Text(), nullable=False, server_default="auto"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
    # No FK: demo datasets are config-driven and absent from `datasets`.
    sa.Column("dataset_id", sa.Text(), nullable=False),
    sa.Column("job_type", sa.Text(), nullable=False, server_default="benchmark"),
    sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
    sa.Column("config_json", sa.Text()),
    sa.Column("run_id", sa.Text()),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("started_at", sa.Text()),
    sa.Column("completed_at", sa.Text()),
    sa.Column("error", sa.Text()),
    sa.Column("metrics_cache", sa.JSON()),
    sa.Column("worker_id", sa.Text()),
    sa.Column("worker_pid", sa.Integer()),
    sa.Column("workload_pid", sa.Integer()),
    sa.Column("process_group_id", sa.Integer()),
    sa.Column("heartbeat_at", sa.Text()),
    sa.Column("cancel_requested_at", sa.Text()),
    sa.Column("exit_code", sa.Integer()),
    sa.Column("visibility", sa.Text(), nullable=False, server_default="private"),
    sa.Column("runner", sa.Text()),
    sa.Column("runner_handle", sa.JSON()),
    sa.Column("artifacts_published_at", sa.Text()),
    sa.Column("runner_request", sa.JSON()),
    sa.Column("timeout_seconds", sa.Integer()),
    sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("failure_category", sa.Text()),
)

job_artifacts = sa.Table(
    "job_artifacts",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "job_id",
        sa.Text(),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("filename", sa.Text(), nullable=False),
    sa.Column("media_type", sa.Text(), nullable=False),
    sa.Column("size_bytes", sa.Integer(), nullable=False),
    sa.Column("checksum", sa.Text(), nullable=False),
    sa.Column("storage_key", sa.Text(), nullable=False),
    sa.Column("created_at", sa.Text(), nullable=False),
)

# Persistent settings overlay written by the admin Settings UI. Lives in the
# database (not the container's ephemeral config.yaml) so admin changes survive
# redeploys. One row per overridden setting; `value` round-trips scalars as JSON.
app_config = sa.Table(
    "app_config",
    metadata,
    sa.Column("key", sa.Text(), primary_key=True),
    sa.Column("value", sa.JSON(), nullable=False),
)
