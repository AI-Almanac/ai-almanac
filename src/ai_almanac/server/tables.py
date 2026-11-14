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

import json

import sqlalchemy as sa

metadata = sa.MetaData()


def json_col[T](value: object, default: T) -> T:
    """A JSON column's value read either way: parsed through these tables, or the
    raw text a ``text()`` query returns. Empty or NULL reads as ``default``."""
    if isinstance(value, str):
        return json.loads(value) if value.strip() else default
    return default if value is None else value


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
    # A blend's day-level blend trains in its own Modal call and may still be
    # running after the job completes (migration 0029). Status is NULL when it
    # does not train apart from the job: not requested, or run in-process locally.
    sa.Column("day_level_call_id", sa.Text()),
    sa.Column("day_level_status", sa.Text()),
    sa.Column("day_level_error", sa.Text()),
    sa.Column("day_level_completed_at", sa.Text()),
    sa.Column("day_level_published_at", sa.Text()),
)

# Per-user hiding of example jobs: "deleting" a visibility='example' job
# records a row here instead of removing the shared job (migration 0024).
user_hidden_jobs = sa.Table(
    "user_hidden_jobs",
    metadata,
    sa.Column("user_id", sa.Text(), primary_key=True),
    sa.Column("job_id", sa.Text(), primary_key=True),
    sa.Column("created_at", sa.Text(), nullable=False),
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
    # BIGINT: blend outputs pass 2^31 bytes (a 26-year combined table is ~12 GB).
    sa.Column("size_bytes", sa.BigInteger(), nullable=False),
    sa.Column("checksum", sa.Text(), nullable=False),
    sa.Column("storage_key", sa.Text(), nullable=False),
    sa.Column("created_at", sa.Text(), nullable=False),
)

# Trajectory-set generation tracking (repurposed from the dead per-run
# `forecast_runs` table; see migration 0016). One row per trajectory set —
# a `(model_name, init_source, season)` triple — recording generation status,
# provenance, and which init dates have been rolled out and cached.
# `variables`/`lead_hours` are NOT NULL JSON inherited from the original schema;
# the generation service writes empty lists when a set carries none.
forecast_runs = sa.Table(
    "forecast_runs",
    metadata,
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
    # Added in 0016 for trajectory-set tracking.
    sa.Column("init_source", sa.Text()),
    sa.Column("season", sa.Text()),
    sa.Column("covered_init_dates", sa.JSON()),
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

# The provenance ledger and the workspaces that view it (migration 0030).
# `provenance_steps` is append-only; see services/ledger.
workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("description", sa.Text(), nullable=False, server_default=""),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("updated_at", sa.Text(), nullable=False),
    sa.Column("settings_json", sa.Text()),  # services/workspace_settings
)

workspace_items = sa.Table(
    "workspace_items",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("workspace_id", sa.Text(), nullable=False),
    sa.Column("item_kind", sa.Text(), nullable=False),  # artifact | job
    sa.Column("artifact_id", sa.Text()),
    sa.Column("job_id", sa.Text()),
    sa.Column("label", sa.Text()),  # artifacts only: the model's handle in this workspace
    sa.Column("added_at", sa.Text(), nullable=False),
    # The file's name here (NULL: derived from its filename), and whether it is
    # something a result was made from rather than a result.
    sa.Column("title", sa.Text()),
    sa.Column("supporting", sa.Boolean()),
    # Jobs only: "user" when a person added an existing run by hand;
    # NULL when the run was made here, or linked before this was recorded.
    sa.Column("added_by", sa.Text()),
)

provenance_steps = sa.Table(
    "provenance_steps",
    metadata,
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

artifacts = sa.Table(
    "artifacts",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("user_id", sa.Text(), nullable=False),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("format", sa.Text(), nullable=False),
    sa.Column("filename", sa.Text(), nullable=False),
    sa.Column("location_kind", sa.Text(), nullable=False),  # owned | job_output
    sa.Column("path", sa.Text()),  # owned: key under the artifact prefix
    sa.Column("job_id", sa.Text()),  # job_output: (job_id, job_file_kind, filename)
    sa.Column("job_file_kind", sa.Text()),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("size_bytes", sa.BigInteger(), nullable=False),
    sa.Column("summary", sa.JSON(), nullable=False),
    sa.Column("provenance_step_id", sa.Text(), nullable=False),
)

approvals = sa.Table(
    "approvals",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("user_id", sa.Text(), nullable=False),
    sa.Column("session_id", sa.Text()),
    sa.Column("turn_id", sa.Text()),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("payload_hash", sa.Text(), nullable=False),
)
