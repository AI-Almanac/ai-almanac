"""Add scoped chat transcript and artifact tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE chat_sessions
        RENAME COLUMN messages TO provider_state
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE chat_sessions
        ADD COLUMN IF NOT EXISTS scope JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS transcript JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    )
    op.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS chat_artifacts (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            user_id     TEXT NOT NULL REFERENCES users(id),
            kind        TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL
        )
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS chat_artifacts"))
    op.execute(
        sa.text("""
        ALTER TABLE chat_sessions
        DROP COLUMN IF EXISTS transcript,
        DROP COLUMN IF EXISTS scope
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE chat_sessions
        RENAME COLUMN provider_state TO messages
    """)
    )
