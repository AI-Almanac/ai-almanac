"""Add chat_sessions table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id),
            title       TEXT,
            messages    JSONB NOT NULL DEFAULT '[]',
            created_at  TIMESTAMPTZ NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL
        )
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS chat_sessions"))
