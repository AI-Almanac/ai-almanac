"""Repair shared deployment schema drift.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns)


def _create_baseline_tables_if_missing(tables: set[str]) -> None:
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("external_id", sa.Text(), nullable=False, unique=True),
            sa.Column("email", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
        )

    if "datasets" not in tables:
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
        op.create_index("idx_datasets_user_id", "datasets", ["user_id"])

    if "jobs" not in tables:
        op.create_table(
            "jobs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
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
        op.create_index("idx_jobs_user_id", "jobs", ["user_id"])
        op.create_index("idx_jobs_status", "jobs", ["status"])
        op.create_index("idx_jobs_run_id", "jobs", ["run_id"])

    if "chat_sessions" not in tables:
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

    if "chat_artifacts" not in tables:
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

    if "forecast_runs" not in tables:
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
        op.create_index(
            "idx_forecast_runs_user_created",
            "forecast_runs",
            ["user_id", sa.text("created_at DESC")],
        )

    if "data_sources" not in tables:
        op.create_table(
            "data_sources",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column("region", sa.Text()),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("idx_data_sources_kind", "data_sources", ["kind"])
        op.create_index("idx_data_sources_region", "data_sources", ["region"])

    if "regions" not in tables:
        op.create_table(
            "regions",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("display_name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("romp_name", sa.Text()),
            sa.Column("boundary_iso", sa.Text()),
            sa.Column("lat_min", sa.Float()),
            sa.Column("lat_max", sa.Float()),
            sa.Column("lon_min", sa.Float()),
            sa.Column("lon_max", sa.Float()),
            sa.Column("land_only", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("shp_only", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text()),
            sa.CheckConstraint("lat_min IS NULL OR lat_min >= -90", name="ck_regions_lat_min"),
            sa.CheckConstraint("lat_max IS NULL OR lat_max <= 90", name="ck_regions_lat_max"),
            sa.CheckConstraint("lon_min IS NULL OR lon_min >= -180", name="ck_regions_lon_min"),
            sa.CheckConstraint("lon_max IS NULL OR lon_max <= 180", name="ck_regions_lon_max"),
            sa.CheckConstraint(
                "lat_min IS NULL OR lat_max IS NULL OR lat_min < lat_max",
                name="ck_regions_lat_order",
            ),
            sa.CheckConstraint(
                "lon_min IS NULL OR lon_max IS NULL OR lon_min < lon_max",
                name="ck_regions_lon_order",
            ),
        )
        op.create_index("idx_regions_display_name", "regions", ["display_name"])


def _repair_data_sources() -> None:
    _add_column_if_missing(
        "data_sources",
        sa.Column("location_type", sa.Text(), nullable=False, server_default="local_directory"),
    )
    _add_column_if_missing(
        "data_sources",
        sa.Column("status", sa.Text(), nullable=False, server_default="invalid"),
    )
    _add_column_if_missing("data_sources", sa.Column("validation_error", sa.Text()))
    _add_column_if_missing("data_sources", sa.Column("updated_at", sa.Text()))
    _add_column_if_missing("data_sources", sa.Column("owner_id", sa.Text(), nullable=True))
    _add_column_if_missing(
        "data_sources",
        sa.Column("visibility", sa.Text(), nullable=False, server_default="shared"),
    )
    _add_column_if_missing(
        "data_sources",
        sa.Column("origin", sa.Text(), nullable=False, server_default="mounted"),
    )
    _create_index_if_missing("idx_data_sources_owner", "data_sources", ["owner_id"])


def _repair_jobs() -> None:
    for column in (
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
        sa.Column("artifacts_published_at", sa.Text(), nullable=True),
        sa.Column("runner_request", sa.JSON()),
        sa.Column("timeout_seconds", sa.Integer()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_category", sa.Text()),
    ):
        _add_column_if_missing("jobs", column)
    _create_index_if_missing("idx_jobs_visibility", "jobs", ["visibility"])


def _repair_users() -> None:
    for column in (
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("issuer", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("groups", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_login_at", sa.Text()),
    ):
        _add_column_if_missing("users", column)


def _create_job_artifacts_if_missing(tables: set[str]) -> None:
    if "job_artifacts" in tables:
        return
    op.create_table(
        "job_artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_job_artifacts_job_id", "job_artifacts", ["job_id"])


def _create_shared_tables_if_missing(tables: set[str]) -> None:
    if "upload_sessions" not in tables:
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

    if "llm_providers" not in tables:
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

    if "user_llm_profiles" not in tables:
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
        if table_name in tables:
            continue
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


def upgrade() -> None:
    _create_baseline_tables_if_missing(_table_names())
    _repair_data_sources()
    _repair_jobs()
    _repair_users()
    tables = _table_names()
    _create_job_artifacts_if_missing(tables)
    _create_shared_tables_if_missing(tables)


def downgrade() -> None:
    pass
