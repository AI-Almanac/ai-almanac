"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-20
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            external_id TEXT UNIQUE NOT NULL,
            email       TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS datasets (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id),
            name        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            storage_key TEXT,
            error       TEXT,
            created_at  TEXT NOT NULL,
            ready_at    TEXT
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL REFERENCES users(id),
            dataset_id   TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'queued',
            config_json  TEXT,
            run_id       TEXT,
            created_at   TEXT NOT NULL,
            started_at   TEXT,
            completed_at TEXT,
            error        TEXT
        )
    """)
    # Demo datasets are config-driven and not stored in the datasets table,
    # so a FK here causes an IntegrityError for every demo job submission.
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_dataset_id_fkey")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS datasets")
    op.execute("DROP TABLE IF EXISTS users")
