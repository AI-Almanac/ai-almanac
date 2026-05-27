"""Add forecast runs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-14
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS forecast_runs (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES users(id),
            status        TEXT NOT NULL DEFAULT 'queued',
            model_id      TEXT NOT NULL,
            model_name    TEXT NOT NULL,
            init_time     TEXT,
            variables     JSONB NOT NULL,
            lead_hours    JSONB NOT NULL,
            storage_prefix TEXT NOT NULL,
            manifest_key  TEXT,
            config_json   JSONB,
            created_at    TEXT NOT NULL,
            started_at    TEXT,
            completed_at  TEXT,
            error         TEXT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_forecast_runs_user_created "
        "ON forecast_runs(user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_forecast_runs_user_created")
    op.execute("DROP TABLE IF EXISTS forecast_runs")
