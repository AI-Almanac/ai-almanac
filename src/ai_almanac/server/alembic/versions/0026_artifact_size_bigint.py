"""Widen job_artifacts.size_bytes to BIGINT.

A 26-year India 0.25° blend writes a ~12 GB combined_wide.pkl. On Postgres the
INTEGER column tops out at 2^31 - 1 bytes (~2.1 GB), so indexing that job's
artifacts failed with "integer out of range", rolled back, and retried forever,
leaving the blend with no listed files, summaries, or map. SQLite already stores
any integer in 64 bits, so only Postgres needs the change.

Old code on this schema stays correct: it writes the same Python ints, which a
BIGINT column accepts.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.alter_column(
            "job_artifacts",
            "size_bytes",
            type_=sa.BigInteger(),
            existing_type=sa.Integer(),
            existing_nullable=False,
        )


def downgrade() -> None:
    if _is_postgres():
        op.alter_column(
            "job_artifacts",
            "size_bytes",
            type_=sa.Integer(),
            existing_type=sa.BigInteger(),
            existing_nullable=False,
        )
